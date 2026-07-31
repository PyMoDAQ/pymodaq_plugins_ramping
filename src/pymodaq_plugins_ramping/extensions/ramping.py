from typing import Iterable, TYPE_CHECKING

from qtpy import QtWidgets


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
        {'title': 'Ramp:', 'name': 'ramp', 'type': 'group', 'children': [
            {'title': 'Start:', 'name': 'start', 'type': 'float', 'value': 0.},
            {'title': 'Stop:', 'name': 'stop', 'type': 'float', 'value': 1.},
            {'title': 'Duration:', 'name': 'duration', 'type': 'float', 'value': 10, 'suffix': 's', 'siPrefix': True},
            {'title': 'Time Step:', 'name': 'time_step', 'type': 'float', 'value': 1e-3, 'suffix': 's', 'siPrefix': True},
            {'title': 'Nsteps:', 'name': 'nsteps', 'type': 'int', 'value': 1, 'readonly': True},
        ]}]

    def __init__(self, parent: gutils.DockArea, dashboard):
        super().__init__(parent, dashboard)

        # info: in an extension, if you want to interact with ControlModules you have to use the
        # object: self.modules_manager which is a ModulesManager instance from the dashboard

        self.setup_ui()

    def setup_docks_and_widgets(self):
        """Mandatory method to be subclassed to setup the docks layout
        """
        self.module_dock = Dock('Modules')
        self.module_dock.addWidget(self.modules_manager.settings_tree)
        self.dockarea.addDock(self.module_dock, 'left')

    def do_things_after_experiment_set(self, experiment_name: str, show_dashboard: bool = None):
        self.modules_manager.set_actuators(actuators=self.dashboard.modules_manager.actuators,
                                           selected_actuators=[])
        self.modules_manager.set_detectors(detectors=self.dashboard.modules_manager.detectors,
                                           selected_detectors=[])

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
        self.modules_manager.actuators_changed.connect(self.update_ramp_settings)

    @property
    def actuator(self) -> 'DAQ_Move':
        return self.modules_manager.actuators[0]

    def update_ramp_settings(self, selected_actuators: Iterable[str]):
        self.settings.child('ramp', 'start').setOpts(siPrefix=self.actuator.units)
        self.settings.child('ramp', 'stop').setOpts(siPrefix=self.actuator.units)

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
