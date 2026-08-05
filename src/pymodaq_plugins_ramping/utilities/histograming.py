import dataclasses

import numpy as np

from pathlib import Path

from qtpy import QtWidgets, QtCore
from pymodaq_utils.logger import set_logger, get_module_name
from pymodaq.utils.shared_ui import SharedUI
from pymodaq_data import DataToExport, DataWithAxes, DataCalculated, Axis, DataDistribution
from pymodaq_data.h5modules.data_saving import DataLoader, Node
from pymodaq_gui.h5modules.saving import H5Saver
from pymodaq_gui.managers.parameter_manager import ParameterManager, Parameter
from pymodaq_gui.plotting.data_viewers import ViewerDispatcher
from pymodaq_gui.qt_utils import mkQApp
from pymodaq_gui.utils import DockArea, Dock, CustomApp
from pymodaq_gui.utils.widgets.window import make_window
from pymodaq_utils.math_utils import find_index


logger = set_logger(get_module_name(__file__))


@dataclasses.dataclass
class HistoObject:
    h5saver: H5Saver | str | Path
    node_path: str | Node
    axis_name: str
    start: float
    stop: float
    nbins: str | int


class HistogramPlot(CustomApp):

    to_worker = QtCore.Signal(HistoObject)


    params = [
        {'title': 'H5:', 'name': 'h5info', 'type': 'group', 'children': [
            {'title': 'H5 Path:', 'name': 'h5path', 'type': 'str', 'value': '', 'readonly': True},
            {'title': 'Node:', 'name': 'node_path', 'type': 'list', 'limits': [], 'readonly': True},
            {'title': 'Actuator:', 'name': 'actuator', 'type': 'list', 'limits': [], 'readonly': True},
        ]},
        {'title': 'Histo:', 'name': 'histo', 'type': 'group', 'children': [
            {'title': 'Start:', 'name': 'start', 'type': 'float', 'value': 500.},
            {'title': 'Stop:', 'name': 'stop', 'type': 'float', 'value': 560.},
            {'title': 'AutoBin:', 'name': 'autobin', 'type': 'led', 'value': False},
            {'title': 'Nbin:', 'name': 'nbins', 'type': 'int', 'value': 100, 'readonly': False},
            ]},

    ]
    def __init__(self, dockarea: DockArea | None = None,
                 title='Histogram',
                 with_threading=True):

        super().__init__(dockarea,
                         title=title,
                         create_app_toolbar=True,
                         add_toolbar_break=False)

        self._h5saver: H5Saver | str | Path = None
        self.viewer = ViewerDispatcher(dockarea=dockarea)
        self.worker = HistoWorker()

        self.to_worker.connect(self.worker.compute_histogram)
        self.worker.dte_signal.connect(self.viewer.show_data)
        self.worker.nbins_signal.connect(self.settings.child('histo', 'nbins').setValue)

        if with_threading:
            self.runner_thread = QtCore.QThread()
            self.worker.moveToThread(self.runner_thread)
            self.runner_thread.start()

        self.setup_ui()

    def setup_menus_and_toolbars(self, menubar: QtWidgets.QMenuBar = None):
        pass

    def setup_docks_and_widgets(self):
        pass

    def setup_actions(self):
        self.add_action('show_file', 'Show file content', 'folder_data',
                        tip='Browse the content of the current HDF5 file')

    def connect_things(self):
        self.connect_action('show_file', self.show_file_content)

    def update_h5_saver(self, h5saver: H5Saver | str | Path,
                        node: str = 'RawData/Ramp000'):
        self._settings.sigTreeStateChanged.disconnect(self.parameter_tree_changed)
        if isinstance(h5saver, H5Saver):
            self.settings['h5info', 'h5path'] = str(h5saver.file_path)
            self._h5saver = h5saver
        else:
            self.settings['h5info', 'h5path'] = str(h5saver)
            self._h5saver = H5Saver()
            self._h5saver.init_file(addhoc_file_path=h5saver)
        self.update_node(node)
        self.update_actuator()

    def update_node(self, node_name: str | Node = None):
        if isinstance(node_name, Node):
            node_name = node_name.path
        nodes = []
        with DataLoader(self.h5saver, swmr_mode=True) as dl:
            for ind, _node in enumerate(dl.walk_nodes('/RawData', depth=1, only_groups=True)):
                if ind > 0:
                    nodes.append(_node.path)
        try:
            self._settings.sigTreeStateChanged.disconnect(self.parameter_tree_changed)
        except TypeError:
            pass
        if node_name is None or node_name not in nodes:
            node_name = nodes[0]
        with self.settings.child('h5info', 'node_path').treeChangeBlocker():
            self.settings.child('h5info', 'node_path').setOpts(value=node_name, limits=nodes)
        self.settings['histo', 'nbins'] = self.check_min_axis_size()
        self._settings.sigTreeStateChanged.connect(self.parameter_tree_changed)

    def check_min_axis_size(self) -> int:
        """ Look at the arrays under current node for the minimal navigation size"""
        min_size = None
        with DataLoader(self.h5saver, swmr_mode=True) as dl:
            for ind, node in enumerate(dl.walk_nodes(self.settings['h5info', 'node_path'])):
                if 'shape' in node.attrs:
                    if min_size is None:
                        min_size = node.attrs['shape'][0]
                    else:
                        min_size = min(min_size, node.attrs['shape'][0])
        return min_size
    def update_actuator(self, actuator: str = None):
        actuators = []
        with DataLoader(self.h5saver, swmr_mode=True) as dl:
            for ind, node in enumerate(dl.walk_nodes('/RawData', depth=2, only_groups=True)):
                if ('type' in node.attrs and node.attrs['type'] == 'actuator' and
                len(node.children()) > 0):
                    actuators.append(node.title)
        try:
            self._settings.sigTreeStateChanged.disconnect(self.parameter_tree_changed)
        except TypeError:
            pass
        if actuator is None or actuator not in actuators:
            actuator = actuators[0]
        with self.settings.child('h5info', 'actuator').treeChangeBlocker():
            self.settings.child('h5info', 'actuator').setOpts(value=actuator, limits=actuators)
        self._settings.sigTreeStateChanged.connect(self.parameter_tree_changed)

    def value_changed(self, param: Parameter):
        if param.name() == 'autobin':
            self.settings.child('histo', 'nbins').setOpts(readonly=param.value())
            if param.value():
                self.compute_plot_histogram(self.settings['h5info', 'actuator'])
        elif (param.name() == 'nbins' and not self.settings['histo', 'autobin']
            ):
            self.compute_plot_histogram(self.settings['h5info', 'actuator'])
        elif param.name() == 'node_path':
            self.settings['histo', 'nbins'] = self.check_min_axis_size()
        elif param.name() == 'actuator':
            self.compute_plot_histogram(param.value())

    def compute_plot_histogram(self, xaxis_name: str):
        actuators = self.settings.child('h5info', 'actuator').opts['limits']
        if xaxis_name not in actuators:
            xaxis_name = actuators[0]
        self.settings['h5info', 'actuator'] = xaxis_name

        self.to_worker.emit(
            HistoObject(self._h5saver,
                        self.settings['h5info', 'node_path'],
                        xaxis_name,
                        self.settings['histo', 'start'],
                        self.settings['histo', 'stop'],
                        'auto' if self.settings['histo', 'autobin'] else self.settings['histo', 'nbins']))


class HistoWorker(QtCore.QObject):

    dte_signal = QtCore.Signal(DataToExport)
    nbins_signal = QtCore.Signal(int)

    def __init__(self):
        super().__init__()

    @QtCore.Slot(HistoObject)
    def compute_histogram(self, histo_obj: HistoObject) -> DataToExport:
        axis_name = histo_obj.axis_name
        dte_out = DataToExport('Histogram')
        logger.info('computing histogram')
        file_path = histo_obj.h5saver
        if isinstance(file_path, H5Saver):
            file_path = file_path.file_path
        with DataLoader(file_path, swmr_mode=True) as dl:
            dte = dl.load_all(where=histo_obj.node_path)
        if axis_name not in dte.get_names():
            self.dte_signal.emit(dte_out)
            return dte_out

        xdwa = dte.pop(dte.index_from_name_origin(axis_name))
        ((istart, vstart), (istop, vstop)) = find_index(
            xdwa[0], threshold=[histo_obj.start,
                                histo_obj.stop])

        nav_index = xdwa.nav_indexes[0]
        try:
            xdwa_sliced = xdwa.inav[istart:istop]
        except IndexError:
            xdwa_sliced = xdwa

        # first compute bins from one of the varying signals
        timestamps = xdwa_sliced.get_axis_from_index(nav_index)[0].get_data()

        if histo_obj.nbins == 'auto':
            estimate_nbins = len(np.histogram_bin_edges(dte[0][0], 'auto')) + 1
        else:
            estimate_nbins = histo_obj.nbins

        bin_edges = np.histogram_bin_edges(
            timestamps,
            bins=estimate_nbins)
        if len(bin_edges) - 1 != histo_obj.nbins:
            self.nbins_signal.emit(len(bin_edges + 1))
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
        self.dte_signal.emit(dte_out)
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

    file_path = r'C:\Data\2026\20260804\Dataset_20260804_040.h5'

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
    histo.compute_plot_histogram('Wavelength')

    app.exec()