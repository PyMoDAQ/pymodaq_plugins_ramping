
from pathlib import Path
from time import perf_counter
from typing import Iterable, TYPE_CHECKING, Union, Mapping

from qtpy import QtWidgets, QtCore


from pymodaq.control_modules.daq_viewer import DAQ_Viewer
from pymodaq.utils.h5modules import module_saving
from pymodaq.control_modules.thread_commands import ControlToHardwareMove
from pymodaq.control_modules.utils import ControlModule
from pymodaq.utils.data import DataActuator
from pymodaq.utils.managers.modules import ModuleType
from pymodaq_data import DataToExport, DataWithAxes
from pymodaq_gui import utils as gutils
from pymodaq_gui.h5modules.saving import H5Saver
from pymodaq_gui.utils import DockArea, Dock
from pymodaq_gui.parameter.utils import iter_children
from pymodaq_plugins_ramping.utilities.module_saver import RampSaver
from pymodaq_utils.config import GlobalConfig
from pymodaq_utils.logger import set_logger, get_module_name

from pymodaq.extensions.utils import CustomExt

from pymodaq_plugins_ramping.utilities.ramp_generator import RampGenerator
from pymodaq_utils.utils import ThreadCommand, find_objects_in_list_from_attr_name_val

if TYPE_CHECKING:
    from pymodaq.control_modules.daq_move import DAQ_Move


logger = set_logger(get_module_name(__file__))
config = GlobalConfig()



EXTENSION_NAME = 'Ramp'  # the name that will be displayed in the extension list in the
# dashboard
CLASS_NAME = 'RampExtension'  # this should be the name of your class defined below


class ModuleAndData:

    def __init__(self, module: Union['DAQ_Move', 'DAQ_Viewer'],
                 module_type: ModuleType,
                 h5saver: H5Saver):
        if module_type == ModuleType.Detector:
            self.module_and_data_saver = module_saving.DetectorTimeSaver(module)
        else:
            self.module_and_data_saver = module_saving.ActuatorTimeSaver(module)
        self.module_and_data_saver.h5saver = h5saver

        self.title: str = module.title

    def append_data(self, dte: DataToExport):
        node = self.module_and_data_saver.get_set_node()
        self.module_and_data_saver.add_data(where=node, data=dte)


class SaverWorker(QtCore.QObject):
    n_saved = QtCore.Signal(int)

    def __init__(self, module: RampSaver):
        super().__init__()
        self.module: RampSaver = module
        self._n_saved = 0

        # self.n_saved_timer = QtCore.QTimer()
        # self.n_saved_timer.setInterval(100)
        # self.n_saved_timer.timeout.connect(self.send_n_saved)

    @QtCore.Slot(DataToExport)
    def save_data(self, dte: DataToExport):
        self.module.add_data(dte)
        self._n_saved += 1
        self.n_saved.emit(self._n_saved)

    # def send_n_saved(self):
    #     self.n_saved.emit(self._n_saved)


class RampExtension(CustomExt):
    send_data_signal = QtCore.Signal(DataToExport)
    _worker_done = QtCore.Signal()

    _group_name = 'Ramp'

    params = [
        {'title': 'Actuator:', 'name': 'actuator', 'type': 'list', },
        {'title': 'Detectors:', 'name': 'detectors', 'type': 'itemselect', 'checkbox': True},
        {'title': 'Grab Step:', 'name': 'grab_step', 'type': 'float', 'value': 0.1, 'suffix': 's', 'siPrefix': True},
        {'title': 'Ramp:', 'name': 'ramp', 'type': 'group', 'children': [
            {'title': 'Start:', 'name': 'start', 'type': 'float', 'value': 500.},
            {'title': 'Stop:', 'name': 'stop', 'type': 'float', 'value': 560.},
            {'title': 'Duration:', 'name': 'duration', 'type': 'float', 'value': 40, 'suffix': 's', 'siPrefix': True},
            {'title': 'Velocity:', 'name': 'velocity', 'type': 'float', 'value': 0, 'suffix': '', 'siPrefix': True,
             'readonly': True},
        ]},
        {'title': 'Use Steps:', 'name': 'use_steps', 'type': 'bool', 'value': True},
        {'title': 'Steps:', 'name': 'steps', 'type': 'group', 'children': [
            {'title': 'Time Step:', 'name': 'time_step', 'type': 'float', 'value': 0.2, 'suffix': 's', 'siPrefix': True},
            {'title': 'Nsteps:', 'name': 'nsteps', 'type': 'int', 'value': 1, 'readonly': True},
            {'title': 'Current Step:', 'name': 'step', 'type': 'float', 'value': 300.},
        ]},
        {'title': 'Worker:', 'name': 'worker', 'type': 'group', 'children': [
            {'title': 'Worker Running:', 'name': 'worker_running', 'type': 'led', 'value': False, 'readonly': True},
            {'title': 'Worker tasks:', 'name': 'worker_tasks', 'type': 'int', 'value': 0, 'readonly': True},
        ]},
    ]


    def __init__(self, parent: gutils.DockArea, dashboard):
        super().__init__(parent, dashboard)

        self._start_time: float = None
        self._paused_time: float = None
        self.ramp: RampGenerator = None

        self._n_emitted = 0

        self.ramp_timer = QtCore.QTimer()
        self.ramp_timer.timeout.connect(self.update_ramp)

        self.total_ramp_timer = QtCore.QTimer()
        self.total_ramp_timer.timeout.connect(self.stop_ramp)

        self._actuator: 'DAQ_Move' = None

        self._h5saver: H5Saver = None
        self._module_and_data_saver = RampSaver(self)

        self.setup_ui()

        self.update_n_steps()
        self.update_velocity()

        self.enable_runflow_actions(False)

    @property
    def h5saver(self):
        if self._h5saver is None:
            self._h5saver = H5Saver()
            self._h5saver.settings.child('do_save').hide()
            self._h5saver.settings.child('custom_name').hide()
            self._h5saver.settings['base_name'] = self._group_name
            self._h5saver.new_file_sig.connect(self.create_new_file)

        if self._h5saver.h5_file is None or not self._h5saver.isopen():
            # Check if there's an existing file to reopen
            current_file = self._h5saver.settings['current_h5_file']
            if current_file and Path(current_file).exists():
                self._try_open_existing_file(current_file)
                self._h5saver.init_file(update_h5=False)
            else:
                try:
                    self._h5saver.init_file(update_h5=True)
                except Exception as e:
                    logger.warning(f"Could not initialize h5 file: {e}")
        return self._h5saver

    def create_new_file(self, new_file):
        if new_file:
            self.close_file()
            # Explicitly create a new file (don't reopen existing)
            try:
                self._h5saver.init_file(update_h5=True)
                logger.info(f"Created new h5 file: {self._h5saver.settings['current_h5_file']}")
            except Exception as e:
                logger.error(f"Could not create new h5 file: {e}")

        if hasattr(self, '_module_and_data_saver'):
            self.module_and_data_saver.h5saver = self._h5saver  # force it for detectors to update their h5saver

    def open_file(self):
        """Reopen the current h5 file if it is closed."""
        if self._h5saver is not None and not self._h5saver.isopen():
            current_file = self._h5saver.settings['current_h5_file']
            if current_file and Path(current_file).exists():
                self._try_open_existing_file(current_file)
            else:
                logger.warning('No file to reopen')

    def close_file(self):
        self._h5saver.close_file()

    def _try_open_existing_file(self, current_file: str):
        """Try to open an existing file, asking user what to do if locked."""
        while True:
            try:
                logger.debug(f"Reopening existing h5 file: {current_file}")
                self._h5saver.init_file(addhoc_file_path=current_file)
                break  # Success
            except Exception as e:
                if 'lock' in str(e).lower() or 'errno = 0' in str(e).lower():
                    # File is locked - ask user what to do
                    msg = QtWidgets.QMessageBox()
                    msg.setIcon(QtWidgets.QMessageBox.Icon.Warning)
                    msg.setWindowTitle("File Locked")
                    msg.setText(f"Cannot open file:\n{current_file}\n\n"
                                f"The file may be open in another application.")
                    msg.setInformativeText("Close the file elsewhere and click Retry, "
                                           "or select a different file.")
                    retry_btn = msg.addButton("Retry", QtWidgets.QMessageBox.ButtonRole.ActionRole)
                    new_auto_btn = msg.addButton("New File (Auto)", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
                    browse_btn = msg.addButton("Browse...", QtWidgets.QMessageBox.ButtonRole.ActionRole)
                    msg.addButton(QtWidgets.QMessageBox.StandardButton.Cancel)
                    msg.exec()

                    if msg.clickedButton() == retry_btn:
                        continue  # Try again
                    elif msg.clickedButton() == new_auto_btn:
                        logger.info("User chose to create new file (auto)")
                        self._h5saver.init_file(update_h5=True)
                        break
                    elif msg.clickedButton() == browse_btn:
                        # Let user select an existing file to append to
                        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
                            None, "Select HDF5 File",
                            str(Path(current_file).parent),
                            "HDF5 Files (*.h5);;All Files (*)",
                        )
                        if file_path:
                            logger.info(f"User selected file: {file_path}")
                            try:
                                self._h5saver.init_file(addhoc_file_path=file_path)
                                break
                            except Exception as e2:
                                logger.warning(f"Could not open selected file: {e2}")
                                continue  # Show dialog again
                        else:
                            continue  # User cancelled browse, show dialog again
                    else:
                        # User cancelled - leave h5_file unchanged
                        logger.info("User cancelled file selection - keeping current file state")
                        break
                else:
                    # Other error - fall back to new file
                    logger.warning(f"Could not reopen h5 file: {e}")
                    self._h5saver.init_file(update_h5=True)
                    break

    @property
    def module_and_data_saver(self):
        if (self._module_and_data_saver.h5saver is None
                or not self._module_and_data_saver.h5saver.isopen()):
            self._module_and_data_saver.h5saver = self.h5saver
        return self._module_and_data_saver

    @module_and_data_saver.setter
    def module_and_data_saver(self, mod: RampSaver):
        self._module_and_data_saver = mod
        self._module_and_data_saver.h5saver = self.h5saver

    def setup_saving(self):
        node_name = self.module_and_data_saver.get_set_node(new=True)
        self.h5saver.settings.child('current_scan_name').setValue(node_name)

    def setup_docks_and_widgets(self):
        """Mandatory method to be subclassed to setup the docks layout
        """
        self.settings_dock = Dock('Settings')
        self.settings_dock.addWidget(self.settings_tree)
        self.saving_dock = Dock('Saving')
        self.saving_dock.addWidget(self.h5saver.settings_tree)

        self.dockarea.addDock(self.settings_dock, 'left')
        self.dockarea.addDock(self.saving_dock, 'right', self.settings_dock)

    def do_things_after_experiment_set(self, experiment_name: str, show_dashboard: bool = None):
        super().do_things_after_experiment_set(experiment_name, show_dashboard)
        self.settings.child('actuator').setLimits(self.modules_manager.actuators_name)
        self.settings['detectors'] = dict(all_items=self.modules_manager.detectors_name,
                                          selected=[])

        self.enable_runflow_actions(True)
        self._module_and_data_saver = RampSaver(self)

    def setup_menus_and_toolbars(self, menubar: QtWidgets.QMenuBar = None):
        """Non mandatory method to be subclassed in order to create a menubar
        """
        # todo create and populate menu using actions defined above in self.setup_actions
        pass

    def do_things_after_ui_setup(self):
        self.create_dashboard_toolbar(add_break=False)

    def setup_actions(self):
        """Method where to create actions to be subclassed. Mandatory

        See Also
        --------
        ActionManager.add_action
        """
        self.add_action('ini_positions', 'Init Positions', 'arrows_input',
                        toolbar=self.toolbar, tip='Go to Initial Ramp position')
        self.add_action('start', 'Start', 'motion_play', "Start the Ramping",
                        icon_color=self.get_theme().green, toolbar=self.toolbar)
        self.add_action('stop', 'Stop Scan', 'stop_circle', "Stop the Ramping",
                        icon_color=self.get_theme().red, toolbar=self.toolbar)
        self.add_action('pause', 'Pause Scan', 'pause_circle', "Pause/resume the Ramping",
                        checkable=True, toolbar=self.toolbar,
                        icon_checked_color=self.get_theme().orange)
        self.add_action('save', 'Save', 'save', toolbar=self.toolbar, checkable=True,
                        tip='Save data', checked=True, icon_checked_color=self.get_theme().green,
                        icon_color=self.get_theme().red)

    def connect_things(self):
        """Connect actions and/or other widgets signal to methods"""
        self.connect_action('start', self.go_to_ini_and_start)
        self.connect_action('stop', self.stop_ramp)
        self.connect_action('pause', self.pause_ramp)

        self.connect_action('ini_positions', self.go_to_ini_ramp)

    def go_to_ini_ramp(self):
        actuator_value = DataActuator('ramp',
                                      data=self.settings['ramp', 'start'],
                                      units=self.actuator.units, )
        self.actuator.command_hardware.emit(
            ThreadCommand(ControlToHardwareMove.MOVE_ABS, [actuator_value, True]))

    def go_to_ini_and_start(self):
        self.actuator.move_done_signal.connect(self.start_ramp)
        self.go_to_ini_ramp()

    def start_ramp(self):
        try:
            self.actuator.move_done_signal.disconnect(self.start_ramp)
        except TypeError:
            pass

        try:
            self._worker_done.disconnect(self.terminate_worker)
        except TypeError:
            pass

        if self.settings['use_steps']:
            self.ramp_timer.setInterval(int(self.settings['steps', 'time_step'] * 1000))

        self.total_ramp_timer.setInterval(int(self.settings['ramp', 'duration'] * 1000))
        self.total_ramp_timer.setSingleShot(True)

        self.setup_saving()

        self._n_emitted = 0

        for detector in self.detectors:
            detector.settings['main_settings', 'wait_time'] = self.settings['grab_step'] * 1000

        self.actuator.settings['main_settings', 'refresh_timeout'] = self.settings['grab_step'] * 1000

        if self.runner_thread is not None and self.runner_thread.isRunning():
            self.exit_runner_thread()

        self.runner_thread = QtCore.QThread()
        self.worker = SaverWorker(module=self.module_and_data_saver)
        self.worker.n_saved.connect(self.update_worker_ntask)
        self.send_data_signal.connect(self.worker.save_data)
        self.worker.moveToThread(self.runner_thread)

        self.ramp = self.get_ramp()

        self.runner_thread.start()
        self.settings['worker', 'worker_running'] = True

        # connect data signals to the event loop of the worker thread
        for detector in self.detectors:
            detector.grab_done_signal.connect(self.send_data)
            detector.grab_data(True)
        self.actuator.current_value_signal.connect(self.send_data)
        self.actuator.get_continuous_actuator_value(get_value=True)

        if self.settings['use_steps']:
            self.ramp_timer.start()
            self.update_ramp()
        else:
            actuator_value = DataActuator('ramp',
                                          data=self.settings['ramp', 'stop'],
                                          units=self.actuator.units, )
            self.actuator.command_hardware.emit(
                ThreadCommand(ControlToHardwareMove.MOVE_ABS, [actuator_value, False]))

            self.total_ramp_timer.start()

        self.enable_runflow_actions(False, excepted=('pause', 'stop'))

    def enable_runflow_actions(self, enable=True, excepted: Iterable[str] = ()):
        for action in ('start', 'ini_positions', 'pause', 'stop'):
            if action not in excepted:
                self.set_action_enabled(action, enable)

    @property
    def detectors(self) -> list['DAQ_Viewer']:
        detectors = []
        for detector in self.settings['detectors']['selected']:
            det = self.modules_manager.get_mod_from_name(detector,
                                                         mod=ModuleType.Detector)
            if det is not None:
                detectors.append(det)
        return detectors

    def stop_ramp(self):
        self.ramp_timer.stop()
        self.total_ramp_timer.stop()

        for detector in self.detectors:
            try:
                detector.grab_done_signal.disconnect(self.send_data)
            except TypeError:
                pass
            detector.grab_data(False)
        try:
            self.actuator.current_value_signal.disconnect(self.send_data)
        except TypeError:
            pass

        self.actuator.get_continuous_actuator_value(get_value=False)

        self._start_time = None

        if self.settings['worker', 'worker_tasks'] == 0:
            self.terminate_worker()
        else:
            self._worker_done.connect(self.terminate_worker)

    def terminate_worker(self):
        self.exit_runner_thread()
        self.h5saver.flush()
        self.h5saver.close_file()
        self.enable_runflow_actions(True)
        self.settings['worker', 'worker_running'] = False

    def pause_ramp(self, do_pause=True):
        if do_pause:
            self.ramp_timer.stop()
            self._paused_time = perf_counter()
            for detector in self.detectors:
                detector.grab_done_signal.disconnect(self.send_data)
            self.actuator.current_value_signal.disconnect(self.send_data)
        else:
            self._start_time = perf_counter() - (self._paused_time - self._start_time)

            for detector in self.detectors:
                detector.grab_done_signal.connect(self.send_data)
            self.actuator.current_value_signal.connect(self.send_data)
            self.ramp_timer.start()

    def update_ramp(self):
        if self._start_time is None:
            self._start_time = perf_counter()
        elapsed_time = perf_counter() - self._start_time

        step = self.ramp(elapsed_time)
        self.settings['steps', 'step'] = step
        actuator_value = DataActuator('ramp',
                                     data=step,
                                     units=self.actuator.units,)
        self.actuator.command_hardware.emit(
            ThreadCommand(ControlToHardwareMove.MOVE_ABS, [actuator_value, False]))

        if elapsed_time > self.settings['ramp', 'duration']:
            self.stop_ramp()

    @property
    def actuators(self) -> Iterable['DAQ_Move']:
            return self.modules_manager.actuators_all

    @property
    def actuators_name(self) -> Iterable[str]:
        return self.modules_manager.actuators_name

    @property
    def actuator(self) -> 'DAQ_Move':
        if self._actuator is None:
            self._actuator =  self.modules_manager.get_mod_from_name(
                self.settings['actuator'],
                mod=ModuleType.Actuator)
        return self._actuator

    def update_ramp_settings(self):
        if self.actuator is not None:
            self.settings.child('ramp', 'start').setOpts(suffix=self.actuator.units)
            self.settings.child('ramp', 'stop').setOpts(suffix=self.actuator.units)

            self.settings.child('steps',  'step').setOpts(suffix=self.actuator.units)

    def value_changed(self, param):
        """ Actions to perform when one of the param's value in self.settings is changed from the
        user interface

        For instance:
        if param.name() == 'do_something':
            if param.value():
                print('Do something')
                self.settings.child('main_settings', 'something_done').setValue(False)

        Parameters
        ----------
        param: (Parameter) the parameter whose value just changed
        """
        if param.name() in ('duration', 'time_step'):
            self.update_n_steps()
        elif param.name() == 'actuator':
            self._actuator: 'DAQ_Move' = None
            self.update_ramp_settings()
        elif param.name() == 'use_steps':
            self.settings.child('steps').show(param.value())
        if param.name() in ('duration', 'start', 'stop'):
            self.update_velocity()

    def update_n_steps(self):
        self.settings['steps', 'nsteps'] = self.settings['ramp', 'duration'] / self.settings['steps', 'time_step']

    def update_velocity(self):
        if self.actuator is not None:
            self.settings['ramp', 'velocity'] = (
                    (self.settings['ramp', 'stop'] - self.settings['ramp', 'start']) / self.settings['ramp', 'duration'])
            self.settings.child('ramp', 'velocity').setOpts(suffix=f'{self.actuator.units}/s')

    def get_ramp(self) -> RampGenerator:
        return RampGenerator(self.settings['ramp', 'start'],
                             self.settings['ramp', 'stop'],
                             self.settings['ramp', 'duration'],)

    def send_data(self, dte: DataToExport | DataActuator):
        if isinstance(dte, DataActuator):
            dte = DataToExport(dte.name, data=[dte])
        self.send_data_signal.emit(dte)
        self._n_emitted += 1

    @QtCore.Slot(int)
    def update_worker_ntask(self, n_saved: int):
        n_tasks = self._n_emitted - n_saved
        self.settings['worker', 'worker_tasks'] = n_tasks

        if n_tasks == 0:
            self._worker_done.emit()


    def quit_fun(self):
        super().quit_fun()
        self.h5saver.flush()
        self.h5saver.close()


def main():
    import sys
    from pymodaq_gui.qt_utils import mkQApp
    from pymodaq.dashboard import create_load_dashboard
    from pymodaq.utils.gui_utils.loader_utils import create_extension

    app = mkQApp('Custom Ext')

    win, dashboard = create_load_dashboard()
    win.mainwindow.setVisible(False)

    win_ext, ext = create_extension(dashboard, RampExtension)

    sys.exit(app.exec())


if __name__ == '__main__':
    main()
