from __future__ import annotations

import asyncio
from time import perf_counter

from .base import CommandExecution, CommandRequest, IsolationKind


class HostRunner:
    """Run commands as the current OS user without a security boundary."""

    name = "host"
    isolation = IsolationKind.NONE

    async def run(self, request: CommandRequest) -> CommandExecution:
        started = perf_counter()
        process = await asyncio.create_subprocess_shell(
            request.command,
            cwd=str(request.cwd),
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
            raise

        return CommandExecution(
            exit_code=process.returncode,
            stdout=stdout.decode(errors="replace"),
            stderr=stderr.decode(errors="replace"),
            duration_ms=int((perf_counter() - started) * 1000),
        )
