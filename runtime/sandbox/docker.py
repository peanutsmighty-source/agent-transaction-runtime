from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from pathlib import Path, PurePosixPath
from time import perf_counter
from uuid import uuid4

from .base import CommandExecution, CommandRequest, IsolationKind


class DockerSandboxRunner:
    """Run shell commands in a short-lived, constrained Docker container."""

    name = "docker"
    isolation = IsolationKind.CONTAINER

    def __init__(
        self,
        workspace: Path,
        image: str,
        *,
        docker_executable: str = "docker",
        allow_network: bool = False,
        memory: str = "512m",
        cpus: float = 1.0,
        pids_limit: int = 128,
        tmpfs_size: str = "64m",
        container_user: str | None = None,
    ) -> None:
        if not image.strip():
            raise ValueError("docker_image_required")
        if cpus <= 0:
            raise ValueError("docker_cpus_must_be_positive")
        if pids_limit <= 0:
            raise ValueError("docker_pids_limit_must_be_positive")

        self.workspace = workspace.resolve()
        self.image = image
        self.docker_executable = docker_executable
        self.allow_network = allow_network
        self.memory = memory
        self.cpus = cpus
        self.pids_limit = pids_limit
        self.tmpfs_size = tmpfs_size
        self.container_user = container_user or self._default_container_user()

    @staticmethod
    def _default_container_user() -> str:
        if hasattr(os, "getuid") and hasattr(os, "getgid"):
            return f"{os.getuid()}:{os.getgid()}"
        return "65532:65532"

    def _container_cwd(self, host_cwd: Path) -> PurePosixPath:
        resolved = host_cwd.resolve()
        if resolved != self.workspace and self.workspace not in resolved.parents:
            raise ValueError("cwd_outside_docker_workspace")
        relative = resolved.relative_to(self.workspace)
        return PurePosixPath("/workspace", *relative.parts)

    def build_argv(self, request: CommandRequest, container_name: str) -> list[str]:
        """Build inspectable Docker CLI arguments without starting a process."""

        container_cwd = self._container_cwd(request.cwd)
        mount = f"type=bind,source={self.workspace},target=/workspace"
        network = "bridge" if self.allow_network else "none"
        return [
            self.docker_executable,
            "run",
            "--rm",
            "--name",
            container_name,
            "--pull",
            "never",
            "--network",
            network,
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(self.pids_limit),
            "--memory",
            self.memory,
            "--cpus",
            str(self.cpus),
            "--user",
            self.container_user,
            "--tmpfs",
            f"/tmp:rw,nosuid,nodev,size={self.tmpfs_size}",
            "--env",
            "HOME=/tmp",
            "--mount",
            mount,
            "--workdir",
            str(container_cwd),
            self.image,
            "sh",
            "-lc",
            request.command,
        ]

    async def _force_remove(self, container_name: str) -> None:
        with suppress(OSError, TimeoutError):
            process = await asyncio.create_subprocess_exec(
                self.docker_executable,
                "rm",
                "--force",
                container_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(process.wait(), timeout=5)

    async def run(self, request: CommandRequest) -> CommandExecution:
        started = perf_counter()
        container_name = f"agent-runtime-lab-{uuid4().hex}"
        argv = self.build_argv(request, container_name)
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=request.timeout_seconds
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            await self._force_remove(container_name)
            return CommandExecution(
                exit_code=None,
                stdout="",
                stderr="",
                duration_ms=int((perf_counter() - started) * 1000),
                timed_out=True,
            )
        except asyncio.CancelledError:
            process.kill()
            await process.wait()
            await self._force_remove(container_name)
            raise

        return CommandExecution(
            exit_code=process.returncode,
            stdout=stdout.decode(errors="replace"),
            stderr=stderr.decode(errors="replace"),
            duration_ms=int((perf_counter() - started) * 1000),
        )
