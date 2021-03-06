import pathlib
import pickle
import shutil
import multiprocessing
from typing import List

import pexpect
import paramiko
import datajoint as dj
import pandas as pd



# To start the image, run `sudo docker-compose up -d`
# from this folder. Then find the host IP address
# of the docker container run `ip a` and look for the
# docker0 port - the IP that is listed there is the
# one to enter as host in the Helium API.
SCHEMA_NAME = "dj_calcium"

dj.config["database.host"] = "127.0.0.1"
dj.config["database.user"] = "root"
dj.config["database.password"] = "pw4pblab"
dj.config["external-raw"] = {
    "protocol": "file",
    "location": f"/data/MatlabCode/PBLabToolkit/CalciumDataAnalysis/python-ca-analysis-bloodflow/calcium_bflow_analysis/datajoint/mysql/data/{SCHEMA_NAME}",
}
schema = dj.schema(SCHEMA_NAME, locals())

PATH_TO_POWER8_FROM_CORTEX = "/mnt/pblabfs/pblab/pbuser/calcium_data"
PATH_INSIDE_POWER8 = "/pblab/pblab/pbuser/calcium_data"
POWER8_ADDRESS = "powerlogin.tau.ac.il"
POWER8_USERNAME = "pbuser"
POWER8_PASSWORD = "pablo000!"


# @schema
# class ExpParams(dj.Manual):
#     definition = """
#     exp_id: smallint_unsigned
#     ---
#     date: date
#     experimenter: varchar(1000)
#     mouse_number: varchar(1000)
#     line_injection: varchar(1000)
#     gcamp_type: enum('Fast', 'Slow', 'None')
#     experiment_type: enum('TAC', 'VascularOcc', 'TwoGroups', '3D', 'Other')
#     foldername: varchar(1000)
#     glob = '*.tif' : varchar(1000)
#     condition_reg = '' : varchar(1000)
#     num_of_channels: enum('1', '2', '3', '4')
#     calcium_channel: enum('1', '2', '3', '4')
#     lines: smallint unsigned
#     columns: smallint unsigned
#     z_planes = 0 : smallint unsigned
#     fr = 58.23 : float
#     magnification: float
#     bidirectional: enum('true', 'false')
#     slow_scan_coef = 1.0 : float
#     objective_lens: enum('x10', 'x25')
#     scan_freq = 7929.0 : float
#     cell_radius_x: tinyint unsigned
#     cell_radius_y: tinyint unsigned
#     cell_radius_z = 0 : tinyint unsigned
#     cells_per_patch = 2 : tinyint unsigned
#     """


@schema
class ExppParams(dj.Manual):
    definition = """
    exp_id: smallint_unsigned
    ---
    date: date
    experimenter: varchar(1000)
    """

@schema
class ComputedParams(dj.Computed):
    definition = """
    -> ExpParams
    ---
    px_um_x: float
    px_um_y: float
    px_um_z : float
    files_found: tinyint unsigned
    file_list: longblob
    """

    def make(self, key):
        params = (ExpParams & key).fetch(as_dict=True)[0]

        if params["objective_lens"] == "x25":
            key["px_um_x"] = params["columns"] / (870 * params["magnification"])
            key["px_um_y"] = params["lines"] / (870 * params["magnification"])
            key["px_um_z"] = params["z_planes"] / (870 * params["magnification"])
        else:
            key["px_um_x"] = params["columns"] / (1840 * params["magnification"])
            key["px_um_y"] = params["lines"] / (1840 * params["magnification"])
            key["px_um_z"] = params["z_planes"] / (1840 * params["magnification"])

        folder = pathlib.Path(params["foldername"])
        files_found = 0
        fnames = []
        for file in folder.rglob(params["glob"]):
            if "CHANNEL" in str(file):
                continue
            files_found += 1
            fnames.append(str(file))
        key["files_found"] = files_found
        key["file_list"] = pickle.dumps(fnames)
        self.insert1(key)


@schema
class CaimanResults(dj.Computed):
    definition = """
    -> ExpParams
    ---
    time = CURRENT_TIMESTAMP : timestamp
    deinterleaved: enum('true', 'false')
    caiman_done: enum('true', 'false')
    dff: longblob  # fov x cell x time
    errors: varchar(1000)
    """

    def make(self, key):
        params = (ExpParams & key).fetch(as_dict=True)[0]
        cparams = (ComputedParams & key).fetch(as_dict=True)[0]
        file_list = pickle.loads(cparams["file_list"])
        dest = (pathlib.Path(PATH_TO_POWER8_FROM_CORTEX) / params["exp_id"]).mkdir(
            parents=True
        )
        path_to_folder_from_within_power8 = (
            pathlib.Path(PATH_INSIDE_POWER8) / params["exp_id"]
        )
        self._copy_files_to_power8(file_list, dest)
        shell_script_fname = 'run_caiman.sh'
        self._create_and_populate_qsub_script(
            foldername=dest,
            folder_in_power8=path_to_folder_from_within_power8,
            shell_script_fname=shell_script_fname,
            glob=params["glob"],
            num_of_channels=params["num_of_channels"],
            data_channel=params["calcium_channe"],
            min_SNR=2,
            K=params["cells_per_patch"],
            gSig=(params["cell_radius_x"], params["cell_radius_y"]),
            fr=params["fr"],
        )
        script_name_within_power8 = path_to_folder_from_within_power8 / shell_script_fname
        self._run_caiman(script_name_within_power8)

    def _copy_files_to_power8(self, file_list: List[str], dest: pathlib.Path):
        """
        Transfers the given file_list to a different location in
        a parallel manner.
        """
        destinations = ((file, dest) for file in file_list)
        with multiprocessing.Pool() as pool:
            pool.starmap(shutil.copy2, destinations)

    def _create_and_populate_qsub_script(
        self,
        foldername: pathlib.Path,
        folder_in_power8: str,
        shell_script_fname="run_caiman.sh",
        glob="*.tif",
        num_of_channels=2,
        data_channel=2,
        min_SNR=2,
        K=2,
        gSig=(5, 5),
        fr=30.03,
    ):
        shell_script = foldername / shell_script_fname
        shell_script.touch()
        script_text = f"""
        #!/bin/bash
        #PBS -e /pblab/pblab/pbuser/errors
        #PBS -o /pblab/pblab/pbuser/outputs

        conda init bash
        source ~/.bashrc
        conda activate caiman

        python /pblab/pblab/CaImAn/demos/general/multifile_pipeline.py -f {folder_in_power8} -g {glob} --num-of-channels {num_of_channels} --data-channel {data_channel} --min-SNR {min_SNR} -K {K} --gSig {gSig} --fr {fr}
        """
        shell_script.write_text(script_text)

    def _run_caiman(self, script: pathlib.Path) -> str:
        power8 = self._login_to_power8()
        stdin, stdout, stderr = power8.exec_command(f'qsub -q pablo {str(script)}')
        print(stdout)
        return stdout

    def _login_to_power8(self) -> paramiko.SSHClient:
        """
        Log in to the power8 server and return
        the pexpect object that can run CaImAn.
        """
        power8 = paramiko.SSHClient()
        power8.load_system_host_keys()
        power8.connect(
            POWER8_ADDRESS, username=POWER8_USERNAME, password=POWER8_PASSWORD
        )
        return power8

    def _verify_run_complete(self):
        pass


@schema
class ManualReview(dj.Manual):
    definition = """
    -> ExpParams
    ---
    manually_reviewed: enum('true', 'false')
    invalid_cells: varchar(1000)
    """


if __name__ == "__main__":
    pass

