
from pathlib import Path
from time import perf_counter
from typing import Iterable, TYPE_CHECKING, Union, Mapping
import numpy as np
from qtpy import QtWidgets, QtCore

from pymodaq_data import Q_, DataDim, DataSource

from pymodaq.control_modules.thread_commands import ControlToHardwareMove
from pymodaq.utils.data import DataActuator
from pymodaq.utils.managers.modules import ModuleType
from pymodaq_data import DataToExport, DataWithAxes
from pymodaq_gui import utils as gutils
from pymodaq_gui.h5modules.saving import H5Saver
from pymodaq_gui.utils import DockArea, Dock

from pymodaq_gui.utils.shared_ui import MenuToolbarNames
from pymodaq_plugins_ramping.utilities.histograming import HistogramPlot
from pymodaq_plugins_ramping.utilities.module_saver import RampSaver, GROUP
from pymodaq_utils.config import GlobalConfig
from pymodaq_utils.logger import set_logger, get_module_name

from pymodaq.extensions.utils import CustomExt

from pymodaq_plugins_ramping.utilities.ramp_generator import RampGenerator
from pymodaq_utils.utils import ThreadCommand

if TYPE_CHECKING:
    from pymodaq.control_modules.daq_move import DAQ_Move
    from pymodaq.control_modules.daq_viewer import DAQ_Viewer

logger = set_logger(get_module_name(__file__))
config = GlobalConfig()



EXTENSION_NAME = 'Ramp'  # the name that will be displayed in the extension list in the
# dashboard
CLASS_NAME = 'RampExtension'  # this should be the name of your class defined below


class SaverWorker(QtCore.QObject):
    """ Worker in separated thread receiving the data from the control modules
    and adding them into the enlargeable arrays with the H5file. All this through the
    RampSaver ModuleSaver """
    n_saved = QtCore.Signal(int)

    def __init__(self, module: RampSaver):
        super().__init__()
        self.module: RampSaver = module
        self._n_saved = 0

    @QtCore.Slot(DataToExport)
    def save_data(self, dte: DataToExport):

        self.module.add_data(dte)
        self._n_saved += 1
        self.n_saved.emit(self._n_saved)




class RampExtension(CustomExt):
    send_data_signal = QtCore.Signal(DataToExport)
    _worker_done = QtCore.Signal()

    _h5_base_group_name = 'Ramp'
    _show_h5file_widgets = True
    params = [
        {'title': 'Ramping Actuator:', 'name': 'actuator', 'type': 'list', },
        {'title': 'Detectors to save:', 'name': 'detectors', 'type': 'itemselect', 'checkbox': True},
        {'title': 'Actuators to save:', 'name': 'actuators', 'type': 'itemselect', 'checkbox': True},
        {'title': 'Refresh Grab:', 'name': 'refresh_grab', 'type': 'float', 'value': 50, 'suffix': 'ms',
         'siPrefix': False},
        {'title': 'Refresh Plot:', 'name': 'refresh_plot', 'type': 'float', 'value': 500, 'suffix': 'ms',
         'siPrefix': False},
        {'title': 'Ramp:', 'name': 'ramp', 'type': 'group', 'children': [
            {'title': 'Start:', 'name': 'start', 'type': 'float', 'value': 500.},
            {'title': 'Stop:', 'name': 'stop', 'type': 'float', 'value': 560.},
            {'title': 'Duration:', 'name': 'duration', 'type': 'float', 'value': 40, 'suffix': 'min', 'siPrefix': True,
            'readonly' : True},
            {'title': 'Velocity:', 'name': 'velocity', 'type': 'float', 'value': 0, 'siPrefix': True,
             'readonly': False},
        ]},
        {'title': 'Use Steps:', 'name': 'use_steps', 'type': 'bool', 'value': True},
        {'title': 'Steps:', 'name': 'steps', 'type': 'group', 'children': [
            {'title': 'Time Step:', 'name': 'time_step', 'type': 'float', 'value': 200, 'suffix': 'ms',
             'siPrefix': False},
            {'title': 'Nsteps:', 'name': 'nsteps', 'type': 'int', 'value': 1, 'readonly': True},
            {'title': 'Current Step:', 'name': 'step', 'type': 'float', 'value': 300.},
        ]},
        {'title': 'Worker:', 'name': 'worker', 'type': 'group', 'children': [
            {'title': 'Worker Running:', 'name': 'worker_running', 'type': 'led', 'value': False, 'readonly': True},
            {'title': 'Worker tasks:', 'name': 'worker_tasks', 'type': 'int', 'value': 0, 'readonly': True},
        ]},
    ]


    def __init__(self, parent: gutils.DockArea, dashboard):
        self.histogramer = HistogramPlot(dockarea=parent)

        super().__init__(parent, dashboard, add_toolbar_break=False)

        self._start_time: Q_ = None
        self._paused_time: Q_ = None
        self.ramp: RampGenerator = None

        self._n_emitted = 0

        self.ramp_timer = QtCore.QTimer()
        self.ramp_timer.timeout.connect(self.update_ramp)

        self.wait_after_stop_timer = QtCore.QTimer()
        self.wait_after_stop_timer.setSingleShot(True)
        self.wait_after_stop_timer.timeout.connect(self.go_home)

        self.total_ramp_timer = QtCore.QTimer()
        self.total_ramp_timer.timeout.connect(self.stop_ramp)

        self.histogramer_timer = QtCore.QTimer()
        self.histogramer_timer.timeout.connect(self.update_histogramer)

        self._actuator: 'DAQ_Move' = None

        self._module_and_data_saver = RampSaver(self)
        self.current_node: GROUP | str = None
        self.setup_ui()

        self.update_n_steps()
        self.update_duration()

        self.enable_runflow_actions(False)

    def setup_saving(self):
        node_name = self.module_and_data_saver.get_set_node(new=True)
        self.h5saver.settings.child('current_scan_name').setValue(node_name)
        self.update_file_status_led()

    def setup_docks_and_widgets(self):
        """Mandatory method to be subclassed to setup the docks layout
        """
        self.settings_dock = Dock('Settings')
        self.settings_dock.addWidget(self.settings_tree)
        self.saving_dock = Dock('Saving')
        self.saving_dock.addWidget(self.h5saver.settings_tree)

        self.histogramer_dock = Dock('Histogram')
        self.histogramer_dock.addWidget(self.histogramer.settings_tree)

        self.dockarea.addDock(self.settings_dock, 'left')
        self.dockarea.addDock(self.histogramer_dock, 'bottom', self.settings_dock)
        self.dockarea.addDock(self.saving_dock, 'right', self.settings_dock)
        self.saving_dock.setVisible(False)
        self.populate_status_bar()

    def do_things_after_experiment_set(self, experiment_name: str, show_dashboard: bool = None):
        super().do_things_after_experiment_set(experiment_name, show_dashboard)
        self.settings.child('actuator').setLimits(self.modules_manager.actuators_name)
        self.display_control_modules()

        self.enable_runflow_actions(True)
        self._module_and_data_saver = RampSaver(self)

    def display_control_modules(self):
        selected = self.settings['detectors']['selected']
        self.settings['detectors'] = dict(all_items=self.modules_manager.detectors_name,
                                          selected=selected)
        actuators = self.modules_manager.actuators_name[:]
        selected = self.settings['actuators']['selected']
        if self.settings['actuator'] in actuators:
            actuators.remove(self.settings['actuator'])
        self.settings['actuators'] = dict(all_items=actuators,
                                          selected=selected)

    def setup_menus_and_toolbars(self, menubar: QtWidgets.QMenuBar = None):
        """Non mandatory method to be subclassed in order to create a menubar
        """
        self.add_menu(MenuToolbarNames.FILE, MenuToolbarNames.FILE.capitalize(), parent_menu=menubar)
        self.add_menu(MenuToolbarNames.TOOLS, MenuToolbarNames.TOOLS.capitalize(), parent_menu=menubar)
        self.add_menu('actions', 'Actions', parent_menu=menubar)


    def do_things_after_ui_setup(self):
        self.create_dashboard_toolbar(add_break=False)

    def setup_actions(self):
        """Method where to create actions to be subclassed. Mandatory

        See Also
        --------
        ActionManager.add_action
        """
        self.add_action('ini_positions', 'Init Positions', 'arrows_input',
                        menu='actions',
                        toolbar=self.toolbar, tip='Go to Initial Ramp position')
        self.add_action('start', 'Start', 'motion_play', "Start the Ramping",
                        menu='actions',
                        icon_color=self.get_theme().green, toolbar=self.toolbar)
        self.add_action('stop', 'Stop Scan', 'stop_circle', "Stop the Ramping",
                        menu='actions',
                        icon_color=self.get_theme().red, toolbar=self.toolbar)
        self.add_action('pause', 'Pause Scan', 'pause_circle',
                        menu='actions', tip="Pause/resume the Ramping",
                        checkable=True, toolbar=self.toolbar,
                        icon_checked_color=self.get_theme().orange)
        self._toolbar.addSeparator()
        self.add_action('show_file', 'Show file content', 'folder_data',
                        tip='Browse the content of the current HDF5 file')

        self.add_action('new_file', 'New file', 'add_circle', menu=MenuToolbarNames.FILE, auto_toolbar=False)
        self.add_action('load', 'Open file to append...', 'file_open', menu=MenuToolbarNames.FILE, auto_toolbar=False)
        self.get_menu(MenuToolbarNames.FILE).addSeparator()
        self.add_action('save', 'Save', 'save', toolbar=self.toolbar, checkable=True,
                        tip='Save data', checked=True, icon_checked_color=self.get_theme().green,
                        icon_color=self.get_theme().red)

        self.add_action('show_saving', 'Show Saving Options', 'settings',
                        menu=MenuToolbarNames.TOOLS, checkable=True,
                        toolbar=self.toolbar, tip='Display in a Dock the Saving Settings')
        self.histogramer.set_action_visible('show_file', False)

    def connect_things(self):
        """Connect actions and/or other widgets signal to methods"""
        self.connect_action('start', self.go_to_ini_and_start)
        self.connect_action('stop', self.stop_ramp)
        self.connect_action('pause', self.pause_ramp)

        self.connect_action('ini_positions', self.go_to_ini_ramp)

        self.connect_action('new_file', self.create_new_file)
        self.connect_action('load', lambda: self.load_file())

        self.connect_action('show_file', self.show_file_content)

        self.connect_action('show_saving', self.saving_dock.setVisible)

    def go_to_ini_ramp(self):
        actuator_value = DataActuator('ramp',
                                      data=self.settings['ramp', 'start'],
                                      units=self.actuator.units, )
        self.actuator.command_hardware.emit(
            ThreadCommand(ControlToHardwareMove.MOVE_ABS, [actuator_value, True]))

    def go_to_ini_and_start(self):
        self.actuator.move_done_signal.connect(self.start_ramp)
        self.go_to_ini_ramp()

    def update_histogramer(self):
        self.histogramer.compute_plot_histogram(self.settings['actuator'])

    def start_ramp(self):
        self.wait_after_stop_timer.stop()
        try:
            self.actuator.move_done_signal.disconnect(self.start_ramp)
        except TypeError:
            pass

        try:
            self._worker_done.disconnect(self.terminate_worker)
        except TypeError:
            pass

        if self.settings['use_steps']:
            self.ramp_timer.setInterval(
                int(Q_(self.settings['steps', 'time_step'],
                       self.settings.child('steps', 'time_step').opts['suffix']).to('ms').magnitude))

        self.total_ramp_timer.setInterval(
            int(Q_(self.settings['ramp', 'duration'],
                   self.settings.child('ramp', 'duration').opts['suffix']).to('ms').magnitude))
        self.total_ramp_timer.setSingleShot(True)

        if self.is_action_checked('save'):
            self.setup_saving()
            self.current_node = self.module_and_data_saver.get_last_node('/RawData')
            self.histogramer.update_h5_saver(self.h5saver.file_path,
                                             node=self.current_node,
                                             actuator=self.settings['actuator'],
                                             )
            self.histogramer_timer.setInterval(int(self.settings['refresh_plot']))

        self._n_emitted = 0

        for detector in self.detectors:
            detector.settings['main_settings', 'wait_time'] = self.settings['refresh_grab']
        for actuator in self.actuators:
            actuator.settings['main_settings', 'refresh_timeout'] = self.settings['refresh_grab']
        self.actuator.settings['main_settings', 'refresh_timeout'] = self.settings['refresh_grab']

        if self.runner_thread is not None and self.runner_thread.isRunning():
            self.exit_runner_thread()

        if self.is_action_checked('save'):
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
        for actuator in self.actuators:
            actuator.current_value_signal.connect(self.send_data)
            actuator.get_continuous_actuator_value(get_value=True)
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
        if self.is_action_checked('save'):
            pass
            #self.histogramer_timer.start()
        self.enable_runflow_actions(False, excepted=('pause', 'stop'))

    def enable_runflow_actions(self, enable=True, excepted: Iterable[str] = ()):
        for action in ('start', 'ini_positions', 'pause', 'stop', 'save'):
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

    @property
    def actuators(self) -> Iterable['DAQ_Move']:
        actuators = []
        for actuator in self.settings['actuators']['selected']:
            act = self.modules_manager.get_mod_from_name(actuator,
                                                         mod=ModuleType.Actuator)
            if act is not None:
                actuators.append(act)
        return actuators

    def stop_ramp(self):
        self.ramp_timer.stop()
        self.total_ramp_timer.stop()
        #self.histogramer_timer.stop()
        self.wait_after_stop_timer.setInterval(3600000)
        self.wait_after_stop_timer.start()
        for detector in self.detectors:
            try:
                detector.grab_done_signal.disconnect(self.send_data)
            except TypeError:
                pass
            detector.grab_data(False)

        for actuator in self.actuators:
            try:
                actuator.current_value_signal.disconnect(self.send_data)

            except TypeError:
                pass
            actuator.get_continuous_actuator_value(get_value=False)
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

    def go_home(self):
        self.actuator.move_home()

    def terminate_worker(self):
        self.exit_runner_thread()
        self.h5saver.flush()
        self.h5saver.close_file()
        self.update_file_status_led()
        self.enable_runflow_actions(True)
        self.settings['worker', 'worker_running'] = False
        self.update_histogramer()

    def pause_ramp(self, do_pause=True):
        if do_pause:
            self.ramp_timer.stop()
            #self.histogramer_timer.stop()
            self._paused_time = Q_(perf_counter(), 's')
            for detector in self.detectors:
                detector.grab_done_signal.disconnect(self.send_data)
            for actuator in self.actuators:
                actuator.current_value_signal.disconnect(self.send_data)
            self.actuator.current_value_signal.disconnect(self.send_data)
        else:
            self._start_time = Q_(perf_counter(),'s') - (self._paused_time - self._start_time)

            for detector in self.detectors:
                detector.grab_done_signal.connect(self.send_data)
            for actuator in self.actuators:
                actuator.current_value_signal.connect(self.send_data)
            self.actuator.current_value_signal.connect(self.send_data)
            self.ramp_timer.start()
            if self.is_action_checked('save'):
                #self.histogramer_timer.start()
                pass

    def update_ramp(self):
        if self._start_time is None:
            self._start_time = Q_(perf_counter(), 's')
        elapsed_time = Q_(perf_counter(), 's') - self._start_time

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
            self.display_control_modules()
            self.update_ramp_settings()
        elif param.name() == 'use_steps':
            self.settings.child('steps').show(param.value())
        if param.name() in ('velocity', 'start', 'stop'):
            self.update_duration()

    def update_n_steps(self):
        self.settings['steps', 'nsteps'] = (Q_(self.settings['ramp', 'duration'],
                                               self.settings.child('ramp', 'duration').opts['suffix']) /
                                            Q_(self.settings['steps', 'time_step'],
                                               self.settings.child('steps', 'time_step').opts['suffix'])).to_reduced_units().magnitude

    def update_duration(self):
        if self.actuator is not None:
            if not np.allclose(self.settings['ramp','velocity'], 0):
                self.settings.child('ramp', 'velocity').setOpts(suffix=f'{self.actuator.units}/min')
                self.settings['ramp', 'duration'] = (
                        (self.settings['ramp', 'stop'] - self.settings['ramp', 'start']) / self.settings['ramp', 'velocity'])
                self.settings.child('ramp', 'duration').setOpts(suffix='min')
            else :
                self.settings['ramp', 'duration'] = 0


    def get_ramp(self) -> RampGenerator:
        return RampGenerator(self.settings['ramp', 'start'],
                             self.settings['ramp', 'stop'],
                             Q_(self.settings['ramp', 'duration'],
                                self.settings.child('ramp', 'duration').opts['suffix']))

    def send_data(self, dte: DataToExport | DataActuator):
        if self.is_action_checked('save'):
            if isinstance(dte, DataActuator):
                dte = DataToExport(dte.name, data=[dte])

            # filtering dwa to be saved
            dte_filtered = DataToExport(dte.name)
            for dwa in dte:
                if 'do_save' in dwa.extra_attributes and dwa.do_save:
                    dte_filtered.append(dwa)
                elif self.filter_data_wrt_settings(dwa):
                    dte_filtered.append(dwa)

            self.send_data_signal.emit(dte_filtered)
            self._n_emitted += 1

    def filter_data_wrt_settings(self, dwa: DataWithAxes):
        flag = True
        if not self.h5saver.settings['save_2D']:  # exclude 2D data and above
            flag = flag and not (dwa.dim == DataDim.Data2D or dwa.dim == DataDim.DataND)
        if self.h5saver.settings['save_raw_only']:  # exclude Calculated data
            flag = flag and dwa.source == DataSource.raw
        return flag

    @QtCore.Slot(int)
    def update_worker_ntask(self, n_saved: int):
        n_tasks = self._n_emitted - n_saved
        self.settings['worker', 'worker_tasks'] = n_tasks

        if n_tasks == 0:
            self._worker_done.emit()

    def quit_fun(self):
        self.h5saver.flush()
        self.h5saver.close()
        self.histogramer.quit_fun()
        super().quit_fun()

    def get_app_toolbars(self) -> list[QtWidgets.QToolBar]:
        """ Get the main toolbars widget to be eventually added in the main window toolbararea

        Default is the default toolbar. To be reimplemented if needed
        """
        return [self.toolbar, self.histogramer.toolbar]


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
