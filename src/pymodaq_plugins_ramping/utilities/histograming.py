import dataclasses
from pathlib import Path
import tempfile
from typing import TYPE_CHECKING
import numpy as np



from qtpy import QtWidgets, QtCore

from pymodaq.extensions.custom_ext import CustomExt
from pymodaq.utils.managers.modules import ModulesManager

from pymodaq_plugins_ramping.utilities.module_saver import RampSaver
from pymodaq_utils.logger import set_logger, get_module_name
from pymodaq.utils.shared_ui import SharedUI
from pymodaq_data import DataToExport, DataWithAxes, DataCalculated, Axis
from pymodaq_data.h5modules.data_saving import DataLoader, Node
from pymodaq_gui.h5modules.saving import H5Saver
from pymodaq_gui.managers.parameter_manager import ParameterManager, Parameter
from pymodaq_gui.plotting.data_viewers import ViewerDispatcher
from pymodaq_gui.qt_utils import mkQApp
from pymodaq_gui.utils import DockArea, Dock, CustomApp
from pymodaq_gui.utils.widgets.window import make_window
from pymodaq_utils.math_utils import find_index

if TYPE_CHECKING:
    from pymodaq.scripting import Dashboard


logger = set_logger(get_module_name(__file__))


@dataclasses.dataclass
class HistoObject:
    h5saver: H5Saver | str | Path
    node_path: str | Node
    axis_name: str
    start: float
    stop: float
    nbins: str | int


class HistogramPlot(CustomExt):

    to_worker = QtCore.Signal(HistoObject)
    module_signal = QtCore.Signal(RampSaver)
    add_data_signal = QtCore.Signal(DataToExport)

    params = [
        {'title': 'H5:', 'name': 'h5info', 'type': 'group', 'children': [
            {'title': 'H5 Path:', 'name': 'h5path', 'type': 'str', 'value': '', 'readonly': True},
            {'title': 'Node:', 'name': 'node_path', 'type': 'list', 'limits': [], 'readonly': True},
            {'title': 'Actuator:', 'name': 'actuator', 'type': 'str', 'value': '', 'readonly': True},
        ]},
        {'title': 'Histo:', 'name': 'histo', 'type': 'group', 'children': [
            {'title': 'Start:', 'name': 'start', 'type': 'float', 'value': 500.},
            {'title': 'Stop:', 'name': 'stop', 'type': 'float', 'value': 560.},
            {'title': 'AutoBin:', 'name': 'autobin', 'type': 'led', 'value': True},
            {'title': 'Nbin:', 'name': 'nbins', 'type': 'int', 'value': 100, 'readonly': True},
            ]},

    ]
    def __init__(self, dockarea: DockArea | None = None,
                 dashboard: 'Dashboard' = None,
                 title='Histogram',):

        super().__init__(dockarea,
                         dashboard,
                         title=title,
                         create_app_toolbar=False,
                         add_toolbar_break=False)

        self._h5saver: H5Saver | str | Path = None
        self.viewer = ViewerDispatcher(dockarea=dockarea)

        self.worker = HistoWorker()

        self.to_worker.connect(self.worker.compute_histogram)
        self.worker.dte_signal.connect(self.viewer.show_data)
        self.worker.nbins_signal.connect(self.settings.child('histo', 'nbins').setValue)
        self.module_signal.connect(self.worker.update_module_saver)
        self.add_data_signal.connect(self.worker.add_data)
        self.runner_thread = QtCore.QThread()
        self.worker.moveToThread(self.runner_thread)
        self.runner_thread.start()

    def create_temp_h5_saver(self):
        self._h5saver = H5Saver()
        self.temp_path = tempfile.TemporaryDirectory(prefix='pymohisto')
        addhoc_file_path = Path(self.temp_path.name).joinpath('temp_data.h5')
        self._h5saver.init_file(custom_naming=True, addhoc_file_path=addhoc_file_path)

        self.module_and_data_saver = RampSaver(self)
        self.module_signal.emit(self.module_and_data_saver)

    def update_h5_saver(self, h5saver: H5Saver | str | Path,
                        node: str = 'RawData/Ramp000'):
        if isinstance(h5saver, H5Saver):
            self.settings['h5info', 'h5path'] = str(h5saver.file_path)

        else:
            self.settings['h5info', 'h5path'] = str(h5saver)
        self._h5saver = h5saver
        self.update_node(node)

    def update_node(self, node_name: str | Node = None,):
        if isinstance(node_name, Node):
            node_name = node_name.path
        self._settings.sigTreeStateChanged.disconnect(self.parameter_tree_changed)
        nodes = []
        with DataLoader(self.h5saver, swmr_mode=True) as dl:
            for ind, _node in enumerate(dl.walk_nodes('/RawData', depth=1, only_groups=True)):
                if ind > 0:
                    nodes.append(_node.path)
        if node_name is None or node_name not in nodes:
            node_name = nodes[0]
        with self.settings.child('h5info', 'node_path').treeChangeBlocker():
            self.settings.child('h5info', 'node_path').setOpts(value=node_name, limits=nodes)
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
            self.compute_plot_histogram(self.settings['h5info', 'actuator'])

    def compute_plot_histogram(self, xaxis_name: str):
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
        self.module_saver: RampSaver = None

    @QtCore.Slot(RampSaver)
    def update_module_saver(self, module: RampSaver):
        self.module_saver = module

    @QtCore.Slot(DataToExport)
    def add_data(self, dte: DataToExport):
        self.module_saver.add_data(dte)

    @QtCore.Slot(HistoObject)
    def compute_histogram(self, histo_obj: HistoObject) -> DataToExport:
        axis_name = histo_obj.axis_name
        dte_out = DataToExport('Histogram')
        logger.info('computing histogram')
        if True:
            saver = self.module_saver.h5saver
        else:
            saver = histo_obj.h5saver
        with DataLoader(saver, swmr_mode=True) as dl:
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
        # first compute bins from the timestamps
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
        averaged_actuator_values = np.atleast_1d(self.average_data_over_indexes(xdwa_sliced[0], indexes))



        for dwa in dte:
            indexes = np.digitize(dwa.get_axis_from_index(nav_index)[0].get_data(), bin_edges)
            dte_out.append(DataCalculated(
                dwa.name, origin=dwa.origin,
                data = [np.atleast_1d(self.average_data_over_indexes(dwa[ind], indexes)) for ind in
                        range(len(dwa))],
                axes = [Axis(label = xdwa.name, units=xdwa.units,
                             data=averaged_actuator_values)] +
                       [dwa.get_axis_from_index(ind)[0] for ind in dwa.sig_indexes],
                labels=dwa.labels,
                units = dwa.units,
            ))
        self.dte_signal.emit(dte_out)
        return dte_out

    @staticmethod
    def average_data_over_indexes(data: np.ndarray[float],
                                  indexes: np.ndarray[int]) -> np.ndarray[float]:
        """ Return the average  of data over the indexes

        See: https://stackoverflow.com/questions/71329884/python-numpy-get-average-of-array-based-on-index
        """
        one_hot = np.eye(np.max(indexes) + 1)[indexes]

        counts = np.sum(one_hot, axis=0)
        return np.sum((one_hot.T * data), axis=1) / counts


if __name__ == '__main__':
    app = mkQApp('Histogram')

    file_path = r'C:\Data\2026\20260804\Dataset_20260804_008.h5'

    win, area = make_window(title='Histogram', flags=None)
    histo = HistogramPlot(dockarea=area)
    histo.settings.child('h5info', 'node_path').setOpts(readonly=False)
    histo.update_h5_saver(file_path)

    dock_settings = Dock('Settings')
    dock_settings.addWidget(histo.settings_tree)
    area.addDock(dock_settings)
    shared_ui = SharedUI(win)
    shared_ui.affect_application(histo)
    histo.compute_plot_histogram('Wavelength')

    app.exec()