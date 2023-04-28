# -*- coding: utf-8 -*-
#
# "TheVirtualBrain - Widgets" package
#
# (c) 2022-2023, TVB Widgets Team
#
"""
A collection of parameter related classes and functions.
- Temporary copy from tvb-inversion package

.. moduleauthor: Fousek Jan <jan.fousek@univ-amu.fr>
.. moduleauthor: Teodora Misan <teodora.misan@codemart.ro>
"""

import os
import json
import sys
import threading
import numpy as np
from copy import deepcopy
from typing import List, Any, Optional, Callable
from dataclasses import dataclass
from tvb.analyzers.metric_variance_global import compute_variance_global_metric
from tvb.analyzers.metric_kuramoto_index import compute_kuramoto_index_metric
from tvb.analyzers.metric_proxy_metastability import compute_proxy_metastability_metric
from tvb.analyzers.metric_variance_of_node_variance import compute_variance_of_node_variance_metric
from tvb.datatypes.connectivity import Connectivity
from tvb.datatypes.time_series import TimeSeries
from tvb.simulator.simulator import Simulator
from joblib import Parallel, delayed
from tvbwidgets.core.pse.pse_data import PSEData, PSEStorage
from tvbwidgets.core.logger.builder import get_logger

# Here put explicit module name string, for __name__ == __main__
LOGGER = get_logger("tvbwidgets.core.pse.parameters")

PROGRESS_BAR_STATUS_FILE = "progress_bar_status.txt"

try:
    from dask.distributed import Client
except ImportError:
    LOGGER.info("ImportError: Dask dependency is not included, so this functionality won't be available")
    Client = object


class ParamGetter:
    pass


@dataclass
class SimSeq:
    """A sequence of simulator configurations."""
    template: Simulator
    params: List[str]
    values: List[List[Any]]
    getters: Optional[List[Optional[ParamGetter]]] = None  # is the first Optional needed?

    # TODO consider transpose, so a param can have a remote data source
    # to load when constructing the sequence

    def __iter__(self):
        self.pos = 0
        return self

    def __post_init__(self):
        self.template.configure()  # deepcopy doesn't work on un-configured simulator o_O
        if self.getters is None:
            self.getters = [None] * len(self.params)
        else:
            assert len(self.getters) == len(self.params)

    def __next__(self):
        if self.pos >= len(self.values):
            raise StopIteration
        obj = deepcopy(self.template)
        updates = zip(self.params, self.getters, self.values[self.pos])
        for key, getter, val in updates:
            if getter is not None:
                val = getter(val)
            exec(f'obj.{key} = val',
                 {'obj': obj, 'val': val})
        self.pos += 1
        return obj


class Metric:
    "A summary statistic for a simulation."

    def __call__(self, t, y) -> np.ndarray:  # what about multi metric returning dict of statistics? Also, chaining?
        pass


class NodeVariability(Metric):
    "A simplistic simulation statistic."

    def __call__(self, t, y):
        return np.std(y[t > (t[-1] / 2), 0, :, 0], axis=0)


class GlobalVariance(Metric):

    def __init__(self, sample_period, start_point=500, segment=4):
        self.sample_period = sample_period
        self.start_point = start_point
        self.segment = segment

    def __call__(self, t, y):
        ts = TimeSeries(sample_period=self.sample_period)
        ts.data = y
        return compute_variance_global_metric({"time_series": ts, "start_point": self.start_point,
                                               "segment": self.segment})


class KuramotoIndex(Metric):

    def __init__(self, sample_period):
        self.sample_period = sample_period

    def __call__(self, t, y):
        ts = TimeSeries(sample_period=self.sample_period)
        ts.data = y
        return compute_kuramoto_index_metric({"time_series": ts})


class ProxyMetastabilitySynchrony(Metric):

    def __init__(self, mode, sample_period, start_point=500, segment=4):
        self.mode = mode
        self.sample_period = sample_period
        self.start_point = start_point
        self.segment = segment

    def __call__(self, t, y):
        ts = TimeSeries(sample_period=self.sample_period)
        ts.data = y
        return compute_proxy_metastability_metric({"time_series": ts, "start_point": self.start_point,
                                                   "segment": self.segment})[self.mode]


class VarianceNodeVariance(Metric):

    def __init__(self, sample_period, start_point=500, segment=4):
        self.sample_period = sample_period
        self.start_point = start_point
        self.segment = segment

    def __call__(self, t, y):
        ts = TimeSeries(sample_period=self.sample_period)
        ts.data = y
        return compute_variance_of_node_variance_metric({"time_series": ts, "start_point": self.start_point,
                                                         "segment": self.segment})


METRICS = ['GlobalVariance', 'KuramotoIndex', 'ProxyMetastabilitySynchrony-Metastability',
           'ProxyMetastabilitySynchrony-Synchrony', 'VarianceNodeVariance']


class Reduction:
    pass


@dataclass
class SaveMetricsToDisk(Reduction):
    filename: str

    def __call__(self, metrics_mat: np.ndarray) -> None:
        np.save(self.filename, metrics_mat)


# or save to a bucket or do SNPE then to a bucket, etc.

@dataclass
class SaveDataToDisk(Reduction):
    param1: str
    param2: str
    x_values: list
    y_values: list
    metrics: list
    file_name: str

    def __call__(self, metric_data: np.ndarray) -> None:
        metrics_data_np = np.array(metric_data)
        pse_result = PSEData()
        pse_result.x_title = self.param1
        pse_result.y_title = self.param2

        if self.param1 == "connectivity":
            id_values = [val.title[0:25] + "..." for val in self.x_values]
            pse_result.x_value = id_values
        else:
            pse_result.x_value = self.x_values
        if self.param2 == "connectivity":
            id_values = [val.title[0:25] + "..." for val in self.y_values]
            pse_result.y_value = id_values
        else:
            pse_result.y_value = self.y_values

        pse_result.metrics_names = self.metrics
        pse_result.results = metrics_data_np.reshape((len(self.metrics), len(self.x_values), len(self.y_values)))

        f = PSEStorage(self.file_name)
        f.store(pse_result)
        LOGGER.info(f"{self.file_name} file created")
        f.close()


@dataclass
class PostProcess:
    metrics: List[Metric]
    reduction: Reduction


class Exec:
    pass


@dataclass
class JobLibExec:
    seq: SimSeq
    post: PostProcess
    backend: Optional[Any]
    checkpoint_dir: Optional[str]
    update_progress: Optional[Callable]
    progress_file_lock: Optional[Any] = threading.Lock()

    def _checkpoint(self, result, i):
        if self.checkpoint_dir is not None:
            np.save(os.path.join(self.checkpoint_dir, f'{i}.npy'), result)

    def _load_checkpoint(self, i):
        if self.checkpoint_dir is None:
            return None
        checkpoint_file = os.path.join(self.checkpoint_dir, f'{i}.npy')
        if not os.path.exists(checkpoint_file):
            return None
        result = np.load(checkpoint_file, allow_pickle=True)
        return result

    def _init_checkpoint(self):
        if self.checkpoint_dir is not None:
            if os.path.exists(self.checkpoint_dir):
                LOGGER.info(f"Reusing existing checkpoint dir {self.checkpoint_dir}")
                # TODO consistency check
            else:
                os.mkdir(self.checkpoint_dir)
                np.savetxt(os.path.join(self.checkpoint_dir, 'params.txt'), self.seq.params, fmt='%s')
                np.save(os.path.join(self.checkpoint_dir, 'param_vals.npy'), self.seq.values)

    def monitor_execution(self):
        try:
            if self.update_progress is not None:
                self.update_progress()
            else:
                with self.progress_file_lock:
                    with open(PROGRESS_BAR_STATUS_FILE, "r+") as f:
                        # set the cursor to the beginning of the file
                        f.seek(0)
                        status = int(f.read())
                        status += 1
                        f.seek(0)
                        f.write(str(status))
        except Exception as e:
            LOGGER.error("Could not update the progress bar status", exc_info=e)


    def __call__(self, n_jobs=-1):
        LOGGER.info("Simulation starts")
        self._init_checkpoint()
        pool = Parallel(n_jobs, prefer="threads")

        @delayed
        def job(sim, i):
            result = self._load_checkpoint(i)
            if result is None:
                if self.backend is not None:
                    runner = self.backend()
                    (t, y), = runner.run_sim(sim.configure())
                else:
                    (t, y), = sim.configure().run()
                result = []
                for m in self.post.metrics:
                    try:
                        result.append(m(t, y))
                    except Exception:
                        result.append(np.nan)
                result = np.hstack(result)
                self._checkpoint(result, i)
            LOGGER.info(f"Task {i} finished")
            self.monitor_execution()
            return result

        metrics_ = pool(job(_, i) for i, _ in enumerate(self.seq))
        LOGGER.info(f"Completed tasks: {pool.n_completed_tasks}")
        self.post.reduction(metrics_)
        LOGGER.info("Local launch finished")


@dataclass
class DaskExec(JobLibExec):

    def __call__(self, client: Client):
        self._init_checkpoint()

        checkpoint_dir = self.checkpoint_dir
        if checkpoint_dir is not None:
            checkpoint_dir = os.path.abspath(checkpoint_dir)

        def _checkpoint(result, i):
            if checkpoint_dir is not None:
                np.save(os.path.join(checkpoint_dir, f'{i}.npy'), result)

        def _load_checkpoint(i):
            if checkpoint_dir is None:
                return None
            checkpoint_file = os.path.join(checkpoint_dir, f'{i}.npy')
            if not os.path.exists(checkpoint_file):
                return None
            result = np.load(checkpoint_file, allow_pickle=True)
            return result

        def job(i, sim):
            result = _load_checkpoint(i)
            if result is None:
                if self.backend is not None:
                    runner = self.backend()
                    (t, y), = runner.run_sim(sim.configure())
                else:
                    (t, y), = sim.configure().run()
                result = np.hstack([m(t, y) for m in self.post.metrics])
                _checkpoint(result, i)
            return result

        def reduction(vals):
            return self.post.reduction(vals)

        metrics_var = client.map(job, *list(zip(*enumerate(self.seq))))

        if self.post.reduction is not None:
            reduced = client.submit(reduction, metrics_var)
            return reduced.result()
        else:
            return metrics_var


def compute_metrics(sim, metrics_):
    computed_metrics = []

    for metric in metrics_:
        if metric == "GlobalVariance":
            resulted_metric = GlobalVariance(sim.monitors[0].period)
        elif metric == "KuramotoIndex":
            resulted_metric = KuramotoIndex(sim.monitors[0].period)
        elif metric == "ProxyMetastabilitySynchrony-Metastability":
            resulted_metric = ProxyMetastabilitySynchrony("Metastability", sim.monitors[0].period)
        elif metric == "ProxyMetastabilitySynchrony-Synchrony":
            resulted_metric = ProxyMetastabilitySynchrony("Synchrony", sim.monitors[0].period)
        else:
            resulted_metric = VarianceNodeVariance(sim.monitors[0].period)
        computed_metrics.append(resulted_metric)

    return computed_metrics


def launch_local_param(simulator, param1, param2, x_values, y_values, metrics, file_name,
                       update_progress=None, n_threads=4):
    input_values = []
    for elem1 in x_values:
        for elem2 in y_values:
            if param1 == "conduction_speed" or param1 == "connectivity":
                el1_value = elem1
            else:
                el1_value = np.array([elem1])
            if param2 == "conduction_speed" or param2 == "connectivity":
                el2_value = elem2
            else:
                el2_value = np.array([elem2])
            input_values.append([el1_value, el2_value])

    sim = simulator.configure()  # deepcopy doesn't work on un-configured simulator o_O
    seq = SimSeq(
        template=sim,
        params=[param1, param2],
        values=input_values
    )
    pp = PostProcess(
        metrics=compute_metrics(sim, metrics),
        reduction=SaveDataToDisk(param1, param2, x_values, y_values, metrics, file_name),
    )
    exe = JobLibExec(seq=seq, post=pp, backend=None, checkpoint_dir=None, update_progress=update_progress)
    exe(n_jobs=n_threads)


if __name__ == '__main__':
    param1 = sys.argv[1]
    param2 = sys.argv[2]
    param1_values = json.loads(sys.argv[3])
    param2_values = json.loads(sys.argv[4])
    n = len(sys.argv[5])
    metrics = sys.argv[5][1:n - 1].split(', ')
    file_name = sys.argv[6]
    n_threads = int(sys.argv[7])

    LOGGER.info(f"We are now starting PSE for '{param1}' x '{param2}' on {n_threads} threads\n"
                f"Expect the result in '{file_name}' \n"
                f"{n} Metrics {metrics}")

    # TODO WID-208 deserialize this instance after being passed from the remote launcher
    sim = Simulator(connectivity=Connectivity.from_file()).configure()

    with open(PROGRESS_BAR_STATUS_FILE, "w+") as f:
        f.write("0")

    launch_local_param(sim, param1, param2, param1_values, param2_values, metrics, file_name,
                       n_threads=n_threads)