import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple, Union

from azwebapps import guess_location_from_display_name, print_err


class CmdRun:
    cmd: str
    out: str
    err: str
    rc: int

    def __init__(self, cmd, rc=None, out=None, err=None):
        self.cmd = cmd
        if rc is None:
            print_err(f"run: {cmd}")
            process = subprocess.run(cmd.split(), capture_output=True)
            self.rc = process.returncode
            self.out = process.stdout.decode("utf-8")
            self.err = process.stderr.decode("utf-8")
        else:
            self.err = err or ""
            self.out = out or ""
            self.rc = rc

    def to_list(self) -> List[Any]:
        return [self.cmd, self.rc, self.out, self.err]

    @staticmethod
    def from_list(ll: Iterable[Any]):
        return CmdRun(*ll)

    def __repr__(self):
        return f"CmdRun({json.dumps(self.to_list())[1:-1]})"


class Player:
    """
    >>> p = Player([["a",0,'out','err']])
    >>> p.get("a")
    CmdRun("a", 0, "out", "err")
    >>> p.assert_at_the_end()
    >>> p = Player([["a",0,'out','err']])
    >>> p.assert_at_the_end()
    Traceback (most recent call last):
    ...
    ValueError: idx:0 not at the end:1
    >>> p.get("b")
    Traceback (most recent call last):
    ...
    ValueError: expected:a but called:b
    >>>
    """

    records: List[CmdRun]
    idx: int

    def __init__(self, ll: Iterable[Iterable[Any]]):
        self.idx = 0
        self.records = list(map(CmdRun.from_list, ll))

    def get(self, cmd) -> CmdRun:
        result = self.records[self.idx]
        if result.cmd != cmd:
            raise ValueError(f"expected:{result.cmd} but called:{cmd}")
        else:
            self.idx += 1
            return result

    def assert_at_the_end(self):
        if self.idx != len(self.records):
            raise ValueError(f"idx:{self.idx} not at the end:{len(self.records)}")


RECORDS = "records"
CMD_LINE = "cmdLine"


def parse_recorder_file(file: Path) -> Tuple[List[str], List[List[Any]]]:
    load = json.load(file.open("rt"))
    return load[CMD_LINE], load[RECORDS]


class Recorder:
    file: Path
    content: Dict[str, Any]
    records: List[List[Any]]

    def __init__(self, file: Union[str, Path], cmd_line: List[str]):
        self.file = Path(file)
        self.content = {}
        self.content[CMD_LINE] = cmd_line
        self.records = []
        self.content[RECORDS] = self.records
        self.write()

    def write(self):
        json.dump(self.content, self.file.open("wt"))

    def record(self, run: CmdRun):
        self.records.append(run.to_list())
        self.write()


class Cmd:
    run: CmdRun
    ctx: "c.Context"
    record_to: Recorder
    replay_from: Player

    def q(self, cmd: str, print_out=False, show_err: bool = True):
        if self.replay_from is None:
            self.run = CmdRun(cmd)
            if self.record_to is not None:
                self.record_to.record(self.run)
        else:
            self.run = self.replay_from.get(cmd)
            print_err(f"fake: {cmd}")
        if print_out:
            print_err(self.run.out)
        if show_err and self.run.err:
            print_err(self.run.err)
        if self.run.rc != 0:
            raise ValueError(f"rc:{self.run.rc}")
        return self

    def __init__(self, record_to: Recorder = None, replay_from: Player = None):
        self.record_to = record_to
        self.replay_from = replay_from

    def json(self):
        try:
            return json.loads(self.run.out)
        except:
            print_err(f"not json: {self.run.out}", file=sys.stderr)
            return None

    def text(self):
        return self.run.out


class AzCmd(Cmd):
    def get_location_mapping(self) -> Dict[str, str]:
        all_locations = self.q(f"az account list-locations").json()
        m = {}
        for l in all_locations:
            name = l["name"]
            m[guess_location_from_display_name(l["displayName"])] = name
            m[name] = name
        return m

    def get_acr_list(self):
        config: "c.WebServicesConfig" = self.ctx.config
        return self.q(f"az acr list -g {config.group}").json()

    def get_plan_list(self):
        config: "c.WebServicesConfig" = self.ctx.config
        return self.q(f"az appservice plan list -g {config.group}").json()

    def get_storage_list(self):
        config: "c.WebServicesConfig" = self.ctx.config
        return self.q(f"az storage account list -g {config.group}").json()

    def get_acr_repo_list(self, acr: "c.Acr"):
        return [
            path.split("/")[1]
            for path in self.q(f"az acr repository list -n {acr.name}").json()
        ]

    def show_manifests(self, repo: "c.Repository", acr: "c.Acr" = None):
        if acr is None:
            acr = repo.path.parent(2).get_state()
        return self.q(
            f"az acr repository show-manifests -n {acr.name}"
            f" --repository {acr.name}/{repo.name}"
        ).json()

    def list_storage_keys(self, storage: "c.Storage"):
        config: "c.WebServicesConfig" = self.ctx.config
        return self.q(
            f"az storage account keys list -g {config.group}" f" -n {storage.name}"
        ).json()

    def list_file_shares(self, storage: "c.Storage"):
        return self.q(
            f"az storage share list --account-name {storage.name} ",
            show_err=False,
        ).json()

    def list_services(self):
        config: "c.WebServicesConfig" = self.ctx.config
        return self.q(f"az webapp list --resource-group {config.group}").json()

    def list_webapp_shares(self, service: "c.Service"):
        config: "c.WebServicesConfig" = self.ctx.config
        return self.q(
            f"az webapp config storage-account list "
            f"--resource-group {config.group} --name {service.name}",
            show_err=False,
        ).json()

    def delete_acr_image(self, iv: "c.ImageVer"):
        repo: c.Repository = iv.repo_path.get_config()
        acr: c.Acr = iv.repo_path.parent(2).get_config()
        return self.q(
            f"az acr repository delete --yes -n {acr.name} "
            f"--image {acr.name}/{repo.name}@{iv.digest}"
        ).text()

    def mount_share(self, mount: "c.Mount"):
        config: "c.WebServicesConfig" = self.ctx.config
        service: c.Service = mount.path.parent(2).get_config()
        return self.q(
            f"az webapp config storage-account add "
            f"--resource-group {config.group} --name {service.name} "
            f"--custom-id {mount.default_custom_id()} "
            f"--storage-type AzureFiles --share-name {mount.share} "
            f"--account-name {mount.account} "
            f"--access-key {mount.access_key()} --mount-path {mount.name}"
        ).json()

    # az webapp config storage-account list --resource-group {config.group} --name {ss.name}
    # az webapp config storage-account delete --custom-id {sharec.custom_id} --resource-group {config.group} --name {ss.name}

    def get_service_props(self, ss: "c.ServiceState"):
        config: "c.WebServicesConfig" = self.ctx.config
        return self.q(
            f"az webapp config container show -n {ss.name} -g {config.group}"
        ).json()

    def list_service_props(self, ss: "c.ServiceState"):
        config: "c.WebServicesConfig" = self.ctx.config
        return self.q(
            f"az webapp config container show -n {ss.name} -g {config.group}"
        ).json()

    def create_webapp(self, ss: "c.ServiceState"):
        config: "c.WebServicesConfig" = self.ctx.config
        plan: c.AppServicePlan = ss.path.parent(2).get_config()
        return self.q(
            f"az webapp create -n {ss.name} -g {config.group} "
            f"-p {plan.name} -i {ss.docker_url()}"
        ).json()

    def delete_webapp(self, ss: "c.Service"):
        config: "c.WebServicesConfig" = self.ctx.config
        return self.q(f"az webapp delete -n {ss.name} -g {config.group} ").text()

    def update_webapp_docker(self, ss: "c.ServiceState"):
        config: "c.WebServicesConfig" = self.ctx.config
        return self.q(
            f"az webapp config container set -n {ss.name} "
            f"-g {config.group} -c {ss.docker}"
        ).json()

    def restart_webapp(self, ss: "c.ServiceState"):
        config: "c.WebServicesConfig" = self.ctx.config
        return self.q(f"az webapp restart -n {ss.name} -g {config.group}").text()


import azwebapps.context as c
