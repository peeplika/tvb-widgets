# -*- coding: utf-8 -*-
#
# "TheVirtualBrain - Widgets" package
#
# (c) 2022-2023, TVB Widgets Team
#

import logging
import pyunicore.client
from datetime import datetime
from urllib.error import HTTPError
from pyunicore.helpers.jobs import Status
from pyunicore.credentials import AuthenticationFailedException
from pkg_resources import get_distribution, DistributionNotFound
from tvbwidgets.core.auth import get_current_token
from tvbwidgets.core.hpc.config import HPCConfig

log = logging.getLogger(__name__)


class HPCLaunch(object):
    pip_libraries = 'tvb-widgets tvb-data'
    EXECUTABLE_KEY = 'Executable'
    PROJECT_KEY = 'Project'
    JOB_TYPE_KEY = 'Job type'
    INTERACTIVE_KEY = 'interactive'

    def __init__(self, hpc_config, param1, param2, param1_values, param2_values, metrics, file_name):
        # type: (HPCConfig, str, str, list, list, list, str) -> None
        self.config = hpc_config
        self.param1 = param1
        self.param2 = param2
        self.param1_values = param1_values
        self.param2_values = param2_values
        self.metrics = metrics
        self.file_name = file_name
        # TODO WID-208 link here the serialized simulator in the list of inputs
        self.submit_job("tvbwidgets.core.pse.parameters", [], True)

    @property
    def _activate_command(self):
        return f'source ${self.config.storage_name}/{self.config.env_dir}/{self.config.env_name}/bin/activate'

    @property
    def _module_load_command(self):
        return f'module load {self.config.module_to_load}'

    @property
    def _create_env_command(self):
        return f'cd ${self.config.storage_name}/{self.config.env_dir} ' \
               f'&& rm -rf {self.config.env_name} ' \
               f'&& python -mvenv {self.config.env_name}'

    @property
    def _install_dependencies_command(self):
        return f'pip install -U pip && pip install {self.pip_libraries}'

    def connect_client(self):
        log.info(f"Connecting to {self.config.site}...")
        token = get_current_token()
        transport = pyunicore.client.Transport(token)
        registry = pyunicore.client.Registry(transport, pyunicore.client._HBP_REGISTRY_URL)

        try:
            sites = registry.site_urls
        except Exception:
            log.error("Unicore seems to be down at the moment. "
                      "Please check service availability and try again later")
            return None

        try:
            site_url = sites[self.config.site]
        except KeyError:
            log.error(f'Site {self.config.site} seems to be down for the moment.')
            return None

        try:
            client = pyunicore.client.Client(transport, site_url)
        except (AuthenticationFailedException, HTTPError):
            log.error(f'Authentication to {self.config.site} failed, you might not have permissions to access it.')
            return None

        log.info(f'Authenticated to {self.config.site} with success.')
        return client

    def _check_environment_ready(self, home_storage):
        # Pyunicore listdir method returns directory names suffixed by '/'
        if f"{self.config.env_dir}/" not in home_storage.listdir():
            home_storage.mkdir(self.config.env_dir)
            log.info("Environment directory not found in HOME, will be created.")
            return False

        if f"{self.config.env_dir}/{self.config.env_name}/" not in home_storage.listdir(self.config.env_dir):
            log.info("Environment not found in HOME, will be created.")
            return False

        try:
            # Check whether tvb-widgets is installed in HPC env and if version is updated
            site_packages_path = f'{self.config.env_dir}/{self.config.env_name}/lib/{self.config.python_dir}/site-packages'
            site_packages = home_storage.listdir(site_packages_path)
            files = [file for file in site_packages if "tvb_widgets" in file]
            assert len(files) >= 1
            remote_version = files[0].split("tvb_widgets-")[1].split('.dist-info')[0]
            log.info(f'Found tvb-widgets version: {remote_version} remotely!')

            try:
                local_version = get_distribution("tvb-widgets").version
                if remote_version != local_version:
                    log.info(f"Found a different remote version {remote_version} of tvb-widgets  "
                             f"installed on the HPC environment, than the local {local_version}, "
                             f"we will recreate env from Pipy to hopefully match.")
                    return False
            except DistributionNotFound:
                # If local installation is from sources, then we can not install it remotely from Pypi
                pass

            return True
        except Exception as ex:
            log.exception("could not match tvb-widgets ...")
            log.info("Could not match tvb-widgets installed in the environment, will recreate it.")
            return False

    def _search_for_home_dir(self, client):
        log.info(f"Accessing storages on {self.config.site}...")
        num = 10
        offset = 0
        storages = client.get_storages(num=num, offset=offset)
        while len(storages) > 0:
            for storage in storages:
                if storage.resource_url.endswith(self.config.storage_name):
                    return storage
            offset += num
            storages = client.get_storages(num=num, offset=offset)
        return None

    @staticmethod
    def _format_date_for_job(job):
        date = datetime.strptime(job.properties['submissionTime'], '%Y-%m-%dT%H:%M:%S+%f')
        return date.strftime('%m.%d.%Y, %H_%M_%S')

    def submit_job(self, executable, inputs, do_stage_out):
        client = self.connect_client()
        if client is None:
            log.error(f"Could not connect to {self.config.site}, stopping execution.")
            return

        home_storage = self._search_for_home_dir(client)
        if home_storage is None:
            log.error(f"Could not find a {self.config.storage_name} storage on {self.config.site}, stopping execution.")
            return

        is_env_ready = self._check_environment_ready(home_storage)
        if is_env_ready:
            log.info("Environment is already prepared, it won't be recreated.")
        else:
            log.info(f"Preparing environment in your {self.config.storage_name} folder...")
            job_description = {
                self.EXECUTABLE_KEY: f"{self._module_load_command} && {self._create_env_command} && "
                                     f"{self._activate_command} && {self._install_dependencies_command}",
                self.PROJECT_KEY: self.config.project,
                self.JOB_TYPE_KEY: self.INTERACTIVE_KEY}
            job_env_prep = client.new_job(job_description, inputs=[])
            log.info(f"Job is running at {self.config.site}: {job_env_prep.working_dir.properties['mountPoint']}. "
                     f"Submission time is: {self._format_date_for_job(job_env_prep)}. "
                     f"Waiting for job to finish..."
                     f"It can also be monitored with the 'Unicore tasks stream' tool on the right-side bar.")
            job_env_prep.poll()
            if job_env_prep.properties['status'] == Status.FAILED:
                log.error("Encountered an error during environment setup, stopping execution.")
                return
            log.info("Successfully finished the environment setup.")

        log.info("Launching workflow...")
        job_description = {
            self.EXECUTABLE_KEY: f"{self._module_load_command} && {self._activate_command} && "
                                 f"python -m  {executable} {self.param1} {self.param2} '{self.param1_values}'  "
                                 f"'{self.param2_values}' '{self.metrics}' {self.file_name}",
            self.PROJECT_KEY: self.config.project}
        job_workflow = client.new_job(job_description, inputs=inputs)
        log.info(f"Job is running at {self.config.site}: {job_workflow.working_dir.properties['mountPoint']}. "
                 f"Submission time is: {self._format_date_for_job(job_workflow)}.")
        log.info('Finished remote launch.')

        if do_stage_out:
            self.monitor_job(job_workflow)
        else:
            log.info('You can use "Unicore Tasks Stream" tool to monitor it.')

    def monitor_job(self, job):
        log.info('Waiting for job to finish...'
                 'It can also be monitored interactively with the "Unicore Tasks Stream" tool.')
        job.poll()

        if job.properties['status'] == Status.FAILED:
            log.error("Job finished with errors.")
            return
        log.info("Job finished with success. Staging out the results...")
        self.stage_out_results(job)
        log.info("Finished execution.")

    def stage_out_results(self, job):
        content = job.working_dir.listdir()

        storage_config_file = content.get(self.file_name)
        if storage_config_file is None:
            log.info(f"Could not find file: {self.file_name}")
            log.info("Could not finalize the stage out. "
                     "Please download your results manually using the 'Unicore Tasks Stream' tool.")
        else:
            storage_config_file.download(self.file_name)
            log.info(f"{self.file_name} file has been downloaded successfully.")
