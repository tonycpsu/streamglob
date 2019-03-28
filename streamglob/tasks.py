import logging
logger = logging.getLogger(__name__)
import asyncio
from datetime import datetime, timedelta
from orderedattrdict import AttrDict
import dataclasses

from .player import Player, Downloader
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

        # global state
        # self.pending = asyncio.Queue()
        super().__init__()
        self.to_play = TaskList()
        self.to_download = TaskList()
        self.playing = TaskList()
        self.active = TaskList()
        self.done = TaskList()
        self.current_task_id = 0
        self.started = asyncio.Condition()

    @property
    def max_concurrent_tasks(self):
        return config.settings.tasks.max or self.DEFAULT_MAX_CONCURRENT_TASKS

    def play(self, task, player_spec, helper_spec, **kwargs):

        self.current_task_id +=1
        task.task_id = self.current_task_id
        # task.action = "play"
        task.args = (player_spec, helper_spec)
        task.kwargs = kwargs
        task.result = asyncio.Future()
        self.to_play.append(task)
        return task.result

    def download(self, task, filename, helper_spec, **kwargs):
        self.current_task_id +=1
        task.task_id = self.current_task_id
        # task.action = "download"
        task.args = (filename, helper_spec)
        task.kwargs = kwargs
        task.result = asyncio.Future()
        self.to_download.append(task)
        return task.result

    async def start(self):
        logger.debug("task_manager starting")
        self.worker_task = state.asyncio_loop.create_task(self.worker())
        self.poller_task = state.asyncio_loop.create_task(self.poller())
        self.started.notify_all()

    async def stop(self):
        logger.info("task_manager stopping")
        # import time; time.sleep(1)

        # for a in self.active:
        #     if a.program.progress_stream:
        #         os.close(a.program.progress_stream)
        #         # a.proc.terminate()

        # await self.pending.join()
        self.worker_task.cancel()
        self.poller_task.cancel()
        # print(self.poller_task.exception())

    async def join(self):
        async with self.started:
            await self.started.wait()
            state.asyncio_loop.run_until_complete(
                self.worker_task,
                self.poller_task
            )

    async def worker(self):

        while True:

            async def wait_for_item():
                while True:
                    if len(self.to_play):
                        return self.to_play.pop(0)
                    elif len(self.active) < self.max_concurrent_tasks and len(self.to_download):
                        return self.to_download.pop(0)
                    await asyncio.sleep(self.QUEUE_INTERVAL)

            task = await wait_for_item()

            if isinstance(task, model.PlayMediaTask):
                program = await Player.play(task, *task.args, **task.kwargs)
            elif isinstance(task, model.DownloadMediaTask):
                try:
                    logger.info(f"kwargs: {task.kwargs}")
                    program = await Downloader.download(task, *task.args, **task.kwargs)
                except SGFileExists as e:
                    logger.warn(e)
                    continue
            else:
                raise NotImplementedError
            task.program = program
            task.proc = program.proc
            logger.debug(f"proc: {task.proc}")
            task.pid = program.proc.pid
            logger.debug(f"pid: {task.pid}")
            # logger.info(task.pid)
            task.started = datetime.now()
            task.elapsed = timedelta(0)
            if isinstance(task, model.PlayMediaTask):
                self.playing.append(task)
            elif isinstance(task, model.DownloadMediaTask):
                self.active.append(task)
            else:
                raise NotImplementedError
            await asyncio.sleep(self.QUEUE_INTERVAL)
            # self.pending.task_done()

    async def poller(self):

        while True:
            logger.trace("poller")

            (playing_done, playing) = utils.partition(
                lambda t: t.proc.returncode is None,
                self.playing)

            playing_done = TaskList(playing_done)

            for t in playing_done:
                t.result.set_result(t.proc.returncode)

            self.playing = TaskList(playing)

            (done, active) = utils.partition(
                lambda t: t.proc.returncode is None,
                self.active)

            done_list = TaskList(done)
            active_list = TaskList(active)

            for t in done_list:
                t.result.set_result(t.proc.returncode)
            self.done += done_list
            self.active = active_list

            for s in self.playing + self.active:

                s.elapsed = datetime.now() - s.started
                if hasattr(s.program, "update_progress"):
                    await s.program.update_progress()
                if hasattr(s.program.source, "update_progress"):
                    await s.program.source.update_progress()

            if state.get("tasks_view"):
                state.tasks_view.refresh()
            await asyncio.sleep(self.QUEUE_INTERVAL)


def main():

    import time

    state.asyncio_loop = asyncio.get_event_loop()
    task_manager = TaskManager()
    state.start_task_manager()
    state.stop_task_manager()
    # state.loop.close()
    # await asyncio.sleep(10)
    # time.sleep(10)

if __name__ == "__main__":
    main()
