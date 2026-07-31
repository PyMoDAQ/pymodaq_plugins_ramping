from time import perf_counter
from typing import Iterable, TYPE_CHECKING

from qtpy import QtWidgets, QtCore

from pymodaq.control_modules.thread_commands import ControlToHardwareMove
from pymodaq.utils.data import DataActuator
from pymodaq.utils.managers.modules import ModuleType
from pymodaq_gui import utils as gutils
from pymodaq_gui.utils import DockArea, Dock
from pymodaq_gui.parameter.utils import iter_children
from pymodaq_utils.config import GlobalConfig
from pymodaq_utils.logger import set_logger, get_module_name

from pymodaq.extensions.utils import CustomExt

from pymodaq_plugins_ramping.utilities.ramp_generator import RampGenerator
from pymodaq_utils.utils import ThreadCommand

if TYPE_CHECKING:
    from pymodaq.control_modules.daq_move import DAQ_Move


logger = set_logger(get_module_name(__file__))
config = GlobalConfig()



EXTENSION_NAME = 'Ramp'  # the name that will be displayed in the extension list in the
# dashboard
CLASS_NAME = 'RampExtension'  # this should be the name of your class defined below


class RampExtension(CustomExt):

    params = [
        {'title': 'Actuator:', 'name': 'actuator', 'type': 'list', },
        {'title': 'Detectors:', 'name': 'detectors', 'type': 'itemselect', 'checkbox': True},
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

        self._actuator: 'DAQ_Move' = None

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
        self.ramp_timer = QtCore.QTimer()
        self.ramp_timer.setInterval(int(self.settings['ramp', 'time_step'] *1000))
        self.ramp_timer.timeout.connect(self.update_ramp)

        self.ramp = self.get_ramp()

        self.ramp_timer.start()
        self.set_action_enabled('start', False)

    def stop_ramp(self):
        self.ramp_timer.stop()
        self._start_time = None
        self.set_action_enabled('start', True)

    def pause_ramp(self, do_pause=True):
        if do_pause:
            self.ramp_timer.stop()
            self._paused_time = perf_counter()
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
        print(actuator_value)
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
