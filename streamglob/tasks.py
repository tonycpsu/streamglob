import logging
logger = logging.getLogger(__name__)
import asyncio
from datetime import datetime, timedelta

from .player import Player, Downloader
from .state import *
from . import utils
from . import config

task_manager_task = None

class TaskList(list):

    def remove_by_id(self, task_id):
        for i, t in enumerate(self):
            if t.task_id == task_id:
                del self[i]

class TaskManager(object):

    QUEUE_INTERVAL = 1
    DEFAULT_MAX_CONCURRENT_TASKS = 20

    def __init__(self):

        # global state
        # self.pending = asyncio.Queue()
        self.to_play = TaskList()
        self.to_download = TaskList()
        self.playing = TaskList()
        self.active = TaskList()
        self.done = TaskList()
        self.current_task_id = 0

    @property
    def max_concurrent_tasks(self):
        return config.settings.tasks.max or self.DEFAULT_MAX_CONCURRENT_TASKS

    def play(self, source, player_spec, helper_spec, **kwargs):

        # self.pending.put_nowait(("play", source, (player_spec, helper_spec), kwargs))
        self.current_task_id +=1
        source.task_id = self.current_task_id
        source.action = "play"
        source.args = (player_spec, helper_spec)
        source.kwargs = kwargs
        self.to_play.append(source)

    def download(self, source, filename, helper_spec, **kwargs):

        # self.pending.put_nowait(("download", source, (filename, helper_spec), kwargs))
        self.current_task_id +=1
        source.task_id = self.current_task_id
        source.action = "download"
        source.args = (filename, helper_spec)
        source.kwargs = kwargs
        self.to_download.append(source)

    async def start(self):
        logger.info("task_manager starting")
        self.worker_task = state.asyncio_loop.create_task(self.worker())
        self.poller_task = state.asyncio_loop.create_task(self.poller())

    async def stop(self):
        logger.info("task_manager stopping")
        # import time; time.sleep(1)
        for a in self.active:
            a.proc.terminate()

        # await self.pending.join()
        self.worker_task.cancel()
        self.poller_task.cancel()
        # print(self.poller_task.exception())

    async def worker(self):

        while True:

            async def wait_for_item():
                while True:
                    if len(self.to_play):
                        return self.to_play.pop(0)
                    elif len(self.active) < self.max_concurrent_tasks and len(self.to_download):
                        return self.to_download.pop(0)
                    await asyncio.sleep(self.QUEUE_INTERVAL)

            source = await wait_for_item()
            logger.info(f"{'playing' if source.action == 'play' else 'downloading'} source: {source}")

            if source.action == "play":
                program = await Player.play(source, *source.args, **source.kwargs)
            elif source.action == "download":
                program = await Downloader.download(source, *source.args, **source.kwargs)
            else:
                raise NotImplementedError

            source.program = program
            logger.info(f"program: {source.program}")
            source.proc = program.proc
            logger.info(f"proc: {source.proc}")
            source.pid = program.proc.pid
            # logger.info(source.pid)
            source.started = datetime.now()
            source.elapsed = timedelta(0)
            if source.action == "play":
                self.playing.append(source)
            elif source.action == "download":
                self.active.append(source)
            await asyncio.sleep(self.QUEUE_INTERVAL)
            # self.pending.task_done()

    async def poller(self):

        while True:

            self.playing = list(filter(
                lambda s: s.proc.returncode is None,
                self.playing))

            (done, active) = utils.partition(
                lambda s: s.proc.returncode is None,
                self.active)
            self.done += TaskList(done)
            self.active = TaskList(active)

            for s in self.active:
                s.elapsed = datetime.now() - s.started
                if hasattr(s.program, "update_progress"):
                    await s.program.update_progress()

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
