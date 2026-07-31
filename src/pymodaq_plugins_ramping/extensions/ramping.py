import dataclasses
from queue import Queue
from time import perf_counter
from typing import Iterable, TYPE_CHECKING, Union

from qtpy import QtWidgets, QtCore

from pymodaq.control_modules.daq_viewer import DAQ_Viewer
from pymodaq.utils.h5modules import module_saving
from pymodaq.control_modules.thread_commands import ControlToHardwareMove
from pymodaq.control_modules.utils import ControlModule
from pymodaq.utils.data import DataActuator
from pymodaq.utils.managers.modules import ModuleType
from pymodaq_data import DataToExport
from pymodaq_gui import utils as gutils
from pymodaq_gui.h5modules.saving import H5Saver
from pymodaq_gui.utils import DockArea, Dock
from pymodaq_gui.parameter.utils import iter_children
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

    def __init__(self, queue: Queue[DataToExport],
                 modules: Iterable[ModuleAndData]):
        super().__init__()

        self.queue = queue
        self.modules = modules

    def save_data(self):

        dte = self.queue.get()
        module: ModuleAndData = find_objects_in_list_from_attr_name_val(self.modules, 'title', dte.name)
        module.append_data(dte)




class RampExtension(CustomExt):

    start_saver = QtCore.Signal()

    params = [
        {'title': 'Actuator:', 'name': 'actuator', 'type': 'list', },
        {'title': 'Detectors:', 'name': 'detectors', 'type': 'itemselect', 'checkbox': True},
        {'title': 'Grab Step:', 'name': 'grab_step', 'type': 'float', 'value': 1, 'suffix': 's', 'siPrefix': True},
        {'title': 'Ramp:', 'name': 'ramp', 'type': 'group', 'children': [
            {'title': 'Start:', 'name': 'start', 'type': 'float', 'value': 300.},
            {'title': 'Stop:', 'name': 'stop', 'type': 'float', 'value': 900.},
            {'title': 'Duration:', 'name': 'duration', 'type': 'float', 'value': 100, 'suffix': 's', 'siPrefix': True},
            {'title': 'Time Step:', 'name': 'time_step', 'type': 'float', 'value': 1, 'suffix': 's', 'siPrefix': True},
            {'title': 'Nsteps:', 'name': 'nsteps', 'type': 'int', 'value': 1, 'readonly': True},
        ]}]

    def __init__(self, parent: gutils.DockArea, dashboard):
        super().__init__(parent, dashboard)

        self._start_time: float = None
        self._paused_time: float = None
        self.ramp: RampGenerator = None

        self.ramp_timer = QtCore.QTimer()

        self._actuator: 'DAQ_Move' = None

        self.h5saver = H5Saver()
        self.queue: Queue[DataToExport] = Queue()

        self.setup_ui()

        self.update_n_steps()

    def setup_docks_and_widgets(self):
        """Mandatory method to be subclassed to setup the docks layout
        """
        self.settings_dock = Dock('Settings')
        self.settings_dock.addWidget(self.settings_tree)

        self.dockarea.addDock(self.settings_dock, 'left')

    def do_things_after_experiment_set(self, experiment_name: str, show_dashboard: bool = None):
        super().do_things_after_experiment_set(experiment_name, show_dashboard)
        self.settings.child('actuator').setLimits(self.modules_manager.actuators_name)
        self.settings['detectors'] = dict(all_items=self.modules_manager.detectors_name,
                                          selected=[])

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
        self.add_action('start', 'Start', 'motion_play', "Start the Ramping",
                        icon_color=self.get_theme().green, toolbar=self.toolbar)
        self.add_action('stop', 'Stop Scan', 'stop_circle', "Stop the Ramping",
                        icon_color=self.get_theme().red, toolbar=self.toolbar)
        self.add_action('pause', 'Pause Scan', 'pause_circle', "Pause/resume the Ramping",
                        checkable=True, toolbar=self.toolbar,
                        icon_checked_color=self.get_theme().orange)

    def connect_things(self):
        """Connect actions and/or other widgets signal to methods"""
        self.connect_action('start', self.start_ramp)
        self.connect_action('stop', self.stop_ramp)
        self.connect_action('pause', self.pause_ramp)

    def start_ramp(self):
        self.ramp_timer.setInterval(int(self.settings['ramp', 'time_step'] *1000))
        self.ramp_timer.timeout.connect(self.update_ramp)

        self.h5saver.init_file(update_h5=True)

        modules = []
        for detector in self.detectors:
            detector.settings['main_settings', 'wait_time'] = self.settings['grab_step']

            modules.append(ModuleAndData(detector, ModuleType.Detector, self.h5saver))

        self.actuator.settings['main_settings', 'refresh_timeout'] = self.settings['grab_step']

        modules.append(ModuleAndData(self.actuator, ModuleType.Actuator, self.h5saver))

        self.runner_thread = QtCore.QThread()
        worker = SaverWorker(self.queue, modules)
        worker.moveToThread(self.runner_thread)
        self.runner_thread.worker  = worker
        self.start_saver.connect(worker.save_data)

        self.ramp = self.get_ramp()

        self.runner_thread.start()
        self.start_saver.emit()

        for detector in self.detectors:
            detector.grab_done_signal.connect(self.append_data)
            detector.grab_data(True)
        self.actuator.current_value_signal.connect(self.append_data)
        self.actuator.get_continuous_actuator_value(get_value=True)

        self.ramp_timer.start()

        self.set_action_enabled('start', False)

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
    def actuators(self):

    def stop_ramp(self):
        self.ramp_timer.stop()
        self._start_time = None
        self.set_action_enabled('start', True)

    def pause_ramp(self, do_pause=True):
        if do_pause:
            self.ramp_timer.stop()
            self._paused_time = perf_counter()
            for detector in self.detectors:
                detector.grab_done_signal.disconnect(self.append_data)
            self.actuator.current_value_signal.disconnect(self.append_data)
        else:
            self._start_time = perf_counter() - (self._paused_time - self._start_time)
            self.ramp_timer.start()

    def update_ramp(self):
        if self._start_time is None:
            self._start_time = perf_counter()
        elapsed_time = perf_counter() - self._start_time

        actuator_value = DataActuator('ramp',
                                     data=self.ramp(elapsed_time),
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

    def update_n_steps(self):
        self.settings['ramp', 'nsteps'] = self.settings['ramp', 'duration'] / self.settings['ramp', 'time_step']

    def get_ramp(self) -> RampGenerator:
        return RampGenerator(self.settings['ramp', 'start'],
                             self.settings['ramp', 'stop'],
                             self.settings['ramp', 'duration'],)

    def append_data(self, data: DataToExport | DataActuator):
        if isinstance(data, DataActuator):
            data = DataToExport(data.origin, data=[data])
        self.queue.put(data)


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
