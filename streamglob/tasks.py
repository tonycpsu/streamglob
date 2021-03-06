import logging
logger = logging.getLogger(__name__)
import os
import asyncio
from datetime import datetime, timedelta
from orderedattrdict import AttrDict
import dataclasses
import itertools
import tempfile

from . import player
from .state import *
from .exceptions import *
from .widgets import Observable
from . import utils
from . import config
from . import model
from . import player

task_manager_task = None

class TaskList(list):

    def remove_by_id(self, task_id):
        for i, t in enumerate(self):
            if t.task_id == task_id:
                del self[i]

class TaskManager(Observable):

    QUEUE_INTERVAL = 1
    DEFAULT_MAX_CONCURRENT_TASKS = 20

    def __init__(self):

        super().__init__()
        self.to_play = TaskList()
        self.to_download = TaskList()
        self.playing = TaskList()
        self.active = TaskList()
        self.postprocessing = TaskList()
        self.done = TaskList()
        self.current_task_id = 0
        self.started = asyncio.Condition()

    @property
    def max_concurrent_tasks(self):
        return config.settings.tasks.max or self.DEFAULT_MAX_CONCURRENT_TASKS

    def play(self, task, player_spec, downloader_spec, **kwargs):

        self.current_task_id +=1
        task.task_id = self.current_task_id
        task.args = (player_spec, downloader_spec)
        task.kwargs = kwargs
        task.program = state.event_loop.create_future()
        task.proc = state.event_loop.create_future()
        task.result = state.event_loop.create_future()
        self.to_play.append(task)
        return task

    def download(self, task, downloader_spec, **kwargs):

        self.current_task_id +=1
        task.task_id = self.current_task_id
        task.args = (downloader_spec,)
        task.kwargs = kwargs
        task.program = state.event_loop.create_future()
        task.proc = state.event_loop.create_future()
        task.result = state.event_loop.create_future()
        self.to_download.append(task)
        return task

    async def run(self):
        while True:
            self.worker_task = state.event_loop.create_task(self.worker())
            self.poller_task = state.event_loop.create_task(self.poller())
            for result in await asyncio.gather(
                    self.worker_task, self.poller_task, return_exceptions=True
            ):
                if isinstance(result, Exception):
                    logger.error("Exception: ", exc_info=result)

            logger.trace("sleeping")
            await asyncio.sleep(self.QUEUE_INTERVAL)


    async def start(self):
        logger.debug("task_manager starting")
        self.run_task = state.event_loop.create_task(self.run())
        self.started.notify_all()

    async def stop(self):
        logger.debug("task_manager stopping")
        self.run_task.cancel()

    async def join(self):
        async with self.started:
            await self.started.wait()
            state.event_loop.run_until_complete(
                self.worker_task,
                self.poller_task
            )

    async def worker(self):

        logger.trace("worker")
        async def get_tasks():
            while True:
                if len(self.to_play):
                    yield self.to_play.pop(0)
                elif len(self.active) < self.max_concurrent_tasks and len(self.to_download):
                    yield self.to_download.pop(0)
                else:
                    return
        async for task in get_tasks():
            logger.trace(f"task: {task}")
            if isinstance(task, model.PlayMediaTask):
                # program = await player.Player.play(task, *task.args, **task.kwargs)
                run_task = player.Player.play(task, *task.args, **task.kwargs)
                # ret = state.event_loop.create_task(run_task)
            elif isinstance(task, model.DownloadMediaTask):
                try:
                    task.tempdir = tempfile.mkdtemp(prefix="streamglob")
                    outfile = task.stage_outfile
                    run_task = player.Downloader.download(task, outfile, *task.args, **task.kwargs)
                    task.stage_results.append(outfile)
                except SGFileExists as e:
                    logger.warn(e)
                    continue
            else:
                logger.error(f"not implemented: {program}")
                raise NotImplementedError

            try:
                proc = await run_task
            except Exception as e:
                task.result.set_result(e)
                continue
            task.proc.set_result(proc)
            logger.debug(f"proc: {task.proc}")
            task.pid = proc.pid
            logger.debug(f"pid: {task.pid}")

            task.started = datetime.now()
            task.elapsed = timedelta(0)

            if isinstance(task, model.PlayMediaTask):
                self.playing.append(task)
            elif isinstance(task, model.DownloadMediaTask):
                self.active.append(task)
            else:
                raise NotImplementedError

    async def poller(self):

        (playing_done, playing) = utils.partition(
            lambda t: t.proc.result().returncode is None,
            self.playing)

        playing_done = TaskList(playing_done)
        for task in playing_done:
            task.result.set_result(task.proc.result().returncode)

        self.playing = TaskList(playing)

        (complete, active) = utils.partition(
            lambda t: t.proc.done() and t.proc.result().returncode is None,
            self.active)

        (done, to_postprocess) = utils.partition(
            lambda t: len(t.postprocessors) > 0,
            complete)

        to_postprocess = list(to_postprocess)
        for task in to_postprocess:
            task.reset()

        (postprocessing_done, postprocessing) = utils.partition(
            lambda t: len(t.postprocessors) > 0,
            itertools.chain(self.postprocessing, to_postprocess))

        postprocessing_list = TaskList(postprocessing)

        active_list = TaskList(active)
        done_list = TaskList(itertools.chain(done, postprocessing_done))

        for task in done_list:
            task.finalize()

        self.active = active_list
        self.postprocessing = postprocessing_list
        self.done += done_list

        for task in self.playing + self.active:
            prog = await task.program
            task.elapsed = datetime.now() - task.started
            if hasattr(prog, "update_progress"):
                await prog.update_progress()
            if hasattr(prog.source, "update_progress"):
                await prog.source.update_progress()

        for task in self.postprocessing:
            task.elapsed = datetime.now() - task.started
            if task.program.done():
                program = task.program.result()
                proc = task.proc.result()
                if proc.returncode is not None:
                    logger.debug("postprocessor done")
                    if not os.path.isfile(task.stage_outfile):
                        logger.warn(f"{task.provider} processing stage {task.stage} failed")
                        task.postprocessors = []
                    task.stage_results.append(task.stage_outfile)
                    task.postprocessors.pop(0)
                    if len(task.postprocessors):
                        task.reset()
                else:
                    logger.debug(f"postprocessor still running: {task.program}")
            elif len(task.postprocessors) > 0:
                pp = task.postprocessors[0]

                proc = await player.Postprocessor.process(
                    task, pp,
                    task.stage_infile,
                    task.stage_outfile,
                )

                task.proc.set_result(proc)
                task.pid = proc.pid

        if state.get("tasks_view"):
            state.tasks_view.refresh()

def main():

    import time

    state.event_loop = asyncio.get_event_loop()
    task_manager = TaskManager()
    state.start_task_manager()
    state.stop_task_manager()

if __name__ == "__main__":
    main()
