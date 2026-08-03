from typing import Mapping, Union

from pymodaq_data.data import DataToExport

from pymodaq.utils.h5modules.module_saving import (
    LoggerSaver, GroupModuleType, DetectorTimeSaver, ActuatorTimeSaver, Node)
from aenum import extend_enum

from pymodaq_data.h5modules.backends import GROUP

extend_enum(GroupModuleType, 'RAMP')

class RampSaver(LoggerSaver):
    """Implementation of the ModuleSaver class dedicated to Ramping module

    Parameters
    ----------
    h5saver
    module
    """
    group_type = GroupModuleType.RAMP
    def __init__(self, module):
        super().__init__(module)

        self.modules: dict[str, ActuatorTimeSaver |DetectorTimeSaver] = {}
        self.current_nodes: dict[str, Node] = {}

    def update_after_h5changed(self):
        for module in self._module.modules_manager.detectors_all:
            self.modules[module.title] = DetectorTimeSaver(module)
            self.modules[module.title].h5saver = self.h5saver
        for module in self._module.modules_manager.actuators_all:
            self.modules[module.title] = ActuatorTimeSaver(module)
            self.modules[module.title].h5saver = self.h5saver

    def create_module_group(self, where: str | Node = None):
        if where is None:
            where = self.get_last_node()
        for module in self.modules:
            self.current_nodes[module] = self.modules[module].get_set_node(where)

    def add_data(self, dte: DataToExport):
        """Add data to it's corresponding control module

        The name of the control module is the DataToExport name attribute
        """
        self.modules[dte.name].add_data(self.current_nodes[dte.name], dte)

    def get_set_node(self, where: Union[Node, str] = None, new=False) -> GROUP:
        new_node = super().get_set_node(where, new)
        if new:
            self.create_module_group(new_node)
        return new_node