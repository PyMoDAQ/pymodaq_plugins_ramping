from typing import Iterable, TYPE_CHECKING

from qtpy import QtWidgets

from pymodaq.utils.managers.modules import ModuleType
from pymodaq_gui import utils as gutils
from pymodaq_gui.utils import DockArea, Dock
from pymodaq_gui.parameter.utils import iter_children
from pymodaq_utils.config import GlobalConfig
from pymodaq_utils.logger import set_logger, get_module_name

from pymodaq.extensions.utils import CustomExt

from pymodaq_plugins_ramping.utilities.ramp_generator import RampGenerator

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
            {'title': 'Start:', 'name': 'start', 'type': 'float', 'value': 0.},
            {'title': 'Stop:', 'name': 'stop', 'type': 'float', 'value': 1.},
            {'title': 'Duration:', 'name': 'duration', 'type': 'float', 'value': 10, 'suffix': 's', 'siPrefix': True},
            {'title': 'Time Step:', 'name': 'time_step', 'type': 'float', 'value': 1e-3, 'suffix': 's', 'siPrefix': True},
            {'title': 'Nsteps:', 'name': 'nsteps', 'type': 'int', 'value': 1, 'readonly': True},
        ]}]

    def __init__(self, parent: gutils.DockArea, dashboard):
        super().__init__(parent, dashboard)

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
        self.create_dashboard_toolbar(add_break=False)

    def setup_actions(self):
        """Method where to create actions to be subclassed. Mandatory

        See Also
        --------
        ActionManager.add_action
        """
        pass

    def connect_things(self):
        """Connect actions and/or other widgets signal to methods"""
        pass

    @property
    def actuators(self) -> Iterable['DAQ_Move']:
        return self.modules_manager.actuators_all

    @property
    def actuators_name(self) -> Iterable[str]:
        return self.modules_manager.actuators_name

    @property
    def actuator(self) -> 'DAQ_Move':
        return self.modules_manager.get_mod_from_name(self.settings['actuator'],
                                                      mod=ModuleType.Actuator)

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
