from dataclasses import dataclass, field
from typing import Any

import numpy as np

from pathlib import Path

from qtpy import QtWidgets, QtCore
from qtpy.QtCore import QObject

from packages.pymodaq_gui.tests.h5module_test.saving_test import h5saver
from pymodaq_gui.managers.h5manager import H5Manager
from pymodaq_gui.utils.shared_ui import MenuToolbarNames
from pymodaq_gui.parameter.ioxml import parameter_to_xml_string
from pymodaq_utils.logger import set_logger, get_module_name
from pymodaq.utils.shared_ui import SharedUI
from pymodaq_data import DataToExport, DataWithAxes, DataCalculated, Axis, DataDistribution, DataDim
from pymodaq_data.h5modules.data_saving import DataLoader, Node, DataToExportSaver
from pymodaq_gui.h5modules.saving import H5Saver, GROUP
from pymodaq_gui.managers.parameter_manager import ParameterManager, Parameter
from pymodaq_gui.plotting.data_viewers import ViewerDispatcher
from pymodaq_gui.qt_utils import mkQApp
from pymodaq_gui.utils import DockArea, Dock, CustomApp, select_file
from pymodaq_gui.utils.widgets.window import make_window
from pymodaq_utils.math_utils import find_index


logger = set_logger(get_module_name(__file__))


class H5Histogramming(QObject, ParameterManager):
    params = [
        {'title': 'H5:', 'name': 'h5info', 'type': 'group', 'children': [
            {'title': 'H5 Path:', 'name': 'h5path', 'type': 'str', 'value': '', 'readonly': True},
            {'title': 'Node:', 'name': 'node_path', 'type': 'list', 'limits': [],},

        ]},
        {'title': 'Histo:', 'name': 'histo', 'type': 'group', 'children': [
            {'title': 'Ramping Actuator:', 'name': 'actuator', 'type': 'list', },
            {'title': 'Detectors to Plot:', 'name': 'detectors', 'type': 'itemselect', 'checkbox': True},
            {'title': 'Actuators to Plot:', 'name': 'actuators', 'type': 'itemselect', 'checkbox': True},
            {'title': 'Start:', 'name': 'start', 'type': 'float', 'value': 500.},
            {'title': 'Stop:', 'name': 'stop', 'type': 'float', 'value': 560.},
            {'title': 'AutoBin:', 'name': 'autobin', 'type': 'bool', 'value': True},
            {'title': 'Nbin:', 'name': 'nbins', 'type': 'int', 'value': 100, 'readonly': False},
        ]},
        {'title': 'Compute Histogram', 'name': 'compute_histogram', 'type': 'action'}
    ]

    def __init__(self, h5_manager: H5Manager, viewer: ViewerDispatcher, parent=None):
        QObject.__init__(self, parent)
        ParameterManager.__init__(self)

        self._h5_manager = h5_manager
        self._viewer = viewer
        self._actuators: dict[str, str] = {}
        self._detectors: dict[str, str] = {}
        self._data_loader: DataLoader = None

        self._histogram_processor: HistogramProcessor = None

        self._h5_manager.file_loaded_signal.connect(self.update_settings_from_file)

        self.settings.child('compute_histogram').setOpts(enabled=False)

    @property
    def histogram_processor(self) -> 'HistogramProcessor':
        if self._histogram_processor is None:
            self._histogram_processor = HistogramProcessor(self._h5_manager.h5saver)
            self._histogram_processor.nbins_signal.connect(
                self.settings.child('histo', 'nbins').setValue)

            self.settings.child('compute_histogram').sigActivated.connect(self.update_histogramer)
            self._histogram_processor.data_processed.connect(self._viewer.show_data)
        return self._histogram_processor

    def update_histogramer(self):
        self.histogram_processor.info_for_histogram_signal.emit(
            InfoForHistogram(self.settings['h5info', 'node_path'],
                             xaxis_name=self.settings['histo', 'actuator'],
                             start=self.settings['histo', 'start'],
                             stop=self.settings['histo', 'stop'],
                             other_names=self.settings['histo', 'actuators']['selected'] +
                                         self.settings['histo', 'detectors']['selected'],
                             bins='auto' if self.settings['histo', 'autobin'] else
                             self.settings['histo', 'nbins'],
                             )
        )

    def update_settings_from_file(self, file_path: Path):
        """ Offline mode. Means ramping is not in progress and the h5file was closed and has
        been opened for here
        """
        self.settings.child('compute_histogram').setOpts(enabled=True)
        self.settings['h5info', 'node_path'] = None
        self._data_loader = DataLoader(self._h5_manager.h5saver,
                                       swmr_mode=False)
        self.settings['h5info', 'h5path'] = str(file_path)
        nodes = self.get_main_nodes()
        if len(nodes) > 0:
            self.disconnect_tree()
            self.settings.child('h5info', 'node_path').setLimits(nodes)
            self.connect_tree()
            self.settings['h5info', 'node_path'] = nodes[-1]

    @property
    def data_loader(self) -> DataLoader:
        return DataLoader(self._h5_manager.h5saver, swmr_mode=True)

    def get_main_nodes(self) -> list[str | GROUP]:
        nodes = []
        for ind, _node in enumerate(self.data_loader.walk_nodes('/RawData', depth=1, only_groups=True)):
            if ind > 0:
                nodes.append(_node.path)
        return nodes

    def get_actuators(self, main_node: str | GROUP) -> dict[str, str]:
        self._actuators = {}
        for ind, node in enumerate(self.data_loader.walk_nodes(main_node, depth=1, only_groups=True)):
            if ('type' in node.attrs and node.attrs['type'] == 'actuator' and
                    len(node.children()) > 0):
                self._actuators[node.title] = node.path
        return self._actuators

    def get_detectors(self, main_node: str | GROUP) -> dict[str, str]:
        self._detectors = {}
        for ind, node in enumerate(self.data_loader.walk_nodes(main_node, depth=1, only_groups=True)):
            if ('type' in node.attrs and node.attrs['type'] == 'detector' and
                    len(node.children()) > 0):
                self._detectors[node.title] = node.path
        return self._detectors

    def get_actuator_dwa(self, actuator_name: str) -> DataWithAxes:
        return self.data_loader.load_all(self._actuators[actuator_name])[0]

    def get_detector_dte(self, detector_name: str) -> DataToExport:
        return self.data_loader.load_all(self._actuators[detector_name], with_bkg=False)

    def value_changed(self, param: Parameter):
        if param.name()  == 'node_path':
            if not (param.value() is None or param.value() == '') :
                self.update_control_modules()
                bin_size = self.check_min_axis_size()

                if bin_size is None:
                    self.settings['histo', 'autobin'] = True
                else:
                    self.settings['histo', 'nbins'] = bin_size

        elif param.name() == 'actuator':
            if param.value() in self._actuators:
                self.get_set_bounds(param.value())

        elif param.name()  == 'autobin':
            self.settings.child('histo', 'nbins').setReadonly(param.value())

    def get_set_bounds(self, actuator_name: str):
        dwa = self.data_loader.load_all(where=self._actuators[actuator_name])[0]
        self.settings.child('histo', 'start').setLimits((np.min(dwa[0]), np.max(dwa[0])))
        self.settings.child('histo', 'stop').setLimits((np.min(dwa[0]), np.max(dwa[0])))
        self.settings['histo', 'start'] = np.min(dwa[0])
        self.settings['histo', 'stop'] = np.max(dwa[0])

    def update_control_modules(self):

        self.disconnect_tree()

        actuators = self.get_actuators(self.settings['h5info', 'node_path'])
        detectors = self.get_detectors(self.settings['h5info', 'node_path'])

        actuators_name = list(actuators.keys())
        detectors_name = list(detectors.keys())

        if self.settings['histo', 'actuator'] not in actuators_name:
            actuator_name = actuators_name.pop(0)
        else:
            actuator_name = self.settings['histo', 'actuator']
        self.settings.child('histo', 'actuator').setOpts(limits=actuators_name + [actuator_name])

        self.settings.child('histo', 'actuators').setValue(dict(all_items=actuators_name,
                                                                selected=actuators_name, ))
        self.settings.child('histo', 'detectors').setValue(dict(all_items=detectors_name,
                                                                selected=detectors_name, ))

        self.connect_tree()
        print(actuator_name)
        self.settings['histo', 'actuator'] = actuator_name

    def get_data(self):

        x_dwa = self.get_actuator_dwa(self.settings['histo', 'actuator'])
        dte_0d = DataToExport('Data0D')
        for actuator in self.settings['histo', 'actuators']['selected']:
            dte_0d.append(self.get_actuator_dwa(actuator))
        for detector in self.settings['histo', 'detectors']['selected']:
            dte_0d.append(self.get_detector_dte(detector).get_data_from_dim(DataDim.Data0D))

    def check_min_axis_size(self) -> int | None:
        """ Look at the arrays under current node for the minimal navigation size"""
        min_size = None
        for ind, node in enumerate(self.data_loader.walk_nodes(self.settings['h5info', 'node_path'])):
            if 'shape' in node.attrs:
                if min_size is None:
                    min_size = node.attrs['shape'][0]
                else:
                    min_size = min(min_size, node.attrs['shape'][0])
        return min_size



@dataclass
class InfoForHistogram:
    node_path: str
    xaxis_name: str

    start: float
    stop: float
    other_names: list[str] = field(default_factory=list)

    bins: float | None = None

class HistogramProcessor(QtCore.QObject):

    data_processed = QtCore.Signal(DataToExport)
    info_for_histogram_signal = QtCore.Signal(InfoForHistogram)
    nbins_signal = QtCore.Signal(int)

    def __init__(self, h5saver: H5Saver, parent=None):
        QtCore.QObject.__init__(self, parent)

        self.h5saver = h5saver
        self._data_loader = DataLoader(h5saver, swmr_mode=True)

        self.info_for_histogram_signal.connect(self.compute_histogram)

    def compute_histogram(self, info: InfoForHistogram) -> DataToExport:
        dte_out = DataToExport('DataOut')

        dte = self._data_loader.load_all(where=info.node_path)
        xdwa = dte.pop(dte.index_from_name_origin(info.xaxis_name))

        ((istart, vstart), (istop, vstop)) = find_index(
            xdwa[0], threshold=[info.start, info.stop])

        nav_index = xdwa.nav_indexes[0]
        try:
            xdwa_sliced = xdwa.inav[istart:istop]
        except IndexError:
            xdwa_sliced = xdwa

        # first compute bins from one of the varying signals
        timestamps = xdwa_sliced.get_axis_from_index(nav_index)[0].get_data()
        if info.bins == 'auto':
            nbins = len(np.histogram_bin_edges(dte[0][0], 'auto')) + 1
        else:
            nbins = info.bins

        bin_edges = np.histogram_bin_edges(timestamps, bins=nbins)

        if info.bins == 'auto':
            self.nbins_signal.emit(len(bin_edges) - 1)

        # then compute the bin index for each timestamp
        indexes = np.digitize(timestamps, bin_edges)

        # average actuator data in their corresponding bins:
        averaged_actuator_values = np.atleast_1d(
            self.average_data_over_indexes(xdwa[0], indexes))

        nans = np.isnan(averaged_actuator_values)

        for dwa in dte:
            indexes = np.digitize(dwa.get_axis_from_index(nav_index)[0].get_data(), bin_edges)
            arrays = [np.delete(
                np.atleast_1d(
                self.average_data_over_indexes(dwa[ind],
                                               indexes,
                                               len(averaged_actuator_values)-1)),
                nans, axis=0) for ind in range(len(dwa))]
            try:
                dwa_processed = DataCalculated(
                    dwa.name, origin=dwa.origin,
                    data = arrays,
                    axes = [Axis(label=xdwa.name, units=xdwa.units,
                                 data=np.delete(averaged_actuator_values,
                                                nans, axis=0),
                                 index=nav_index)] +
                           [dwa.get_axis_from_index(ind)[0] for ind in dwa.sig_indexes],
                    labels=dwa.labels,
                    units = dwa.units,
                    nav_indexes=(nav_index,) if len(dwa.sig_indexes) > 0 else ( ),
                    distribution=DataDistribution.uniform,
                )
                dte_out.append(dwa_processed)
            except IndexError as e:
                pass
        self.data_processed.emit(dte_out)
        return dte_out

    @staticmethod
    def average_data_over_indexes(data: np.ndarray[float],
                                  indexes: np.ndarray[int],
                                  index_max: int = None) -> np.ndarray[float]:
        """ Return the average  of data over the indexes

        See: https://stackoverflow.com/questions/71329884/python-numpy-get-average-of-array-based-on-index
        """
        try:
            if index_max is None:
                index_max = indexes.max()

            one_hot = np.eye(index_max + 1)[indexes]

            counts = np.sum(one_hot, axis=0)
            one_hot_t = one_hot.T
            for ind in range(len(data.shape) - len(one_hot_t.shape) + 1):
                one_hot_t = np.expand_dims(one_hot_t, axis=ind+2)
                counts = np.expand_dims(counts, axis=ind+1)
            res = np.sum((one_hot_t * data[0:len(indexes)]), axis=1) / counts
        except ValueError as e:
            res = np.linspace(0, indexes.max(), index_max + 1)
        return res


if __name__ == '__main__':
    app = mkQApp('Histogram')

    file_path = r'C:\Data\2026\20260805\Dataset_20260805_005.h5'

    win, area = make_window(title='Histogram', flags=None)
    histo = HistogramPlot(dockarea=area, with_threading=False)
    histo.settings.child('h5info', 'node_path').setOpts(readonly=False)
    histo.settings.child('h5info', 'actuator').setOpts(readonly=False)
    histo.update_h5_saver(file_path)

    dock_settings = Dock('Settings')
    dock_settings.addWidget(histo.settings_tree)
    area.addDock(dock_settings)
    shared_ui = SharedUI(win)
    shared_ui.affect_application(histo)
    histo.compute_histogram('Wavelength')

    app.exec()