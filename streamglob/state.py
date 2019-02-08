import logging
logger = logging.getLogger(__name__)
from memoize import *
from orderedattrdict import AttrDict
import asyncio

from . import providers
from . import config
from .player import Player


class TaskManager(object):

    QUEUE_INTERVAL = 1

    def __init__(self):

        global state
        self.pending = asyncio.Queue()
        self.active = list()

    def add(self, *task):

        logger.info(f"adding task: {task}")
        self.pending.put_nowait(task)

    def play(self, source, *args, **kwargs):

        self.pending.put_nowait(("play", source, args, kwargs))

    async def start(self):
        logger.info("task_manager starting")
        self.worker_task = state.asyncio_loop.create_task(self.worker())
        self.poller_task = state.asyncio_loop.create_task(self.poller())

    async def stop(self):
        logger.info("task_manager stopping")
        await self.pending.join()
        self.worker_task.cancel()
        self.poller_task.cancel()

    async def worker(self):

        while True:

            logger.info("worker")

            (action, source, args, kwargs) = await self.pending.get()
            # raise Exception
            logger.info(f"{'playing' if action == 'play' else 'downloading'} source: {source}")
            proc = Player.play(source, *args, **kwargs)
            # logger.info(proc)
            source.action = action
            source.proc = proc
            source.pid = proc.pid
            self.active.append(source)

            self.pending.task_done()

    async def poller(self):

        while True:
            logger.info("poller")
            self.active = [ s for s in self.active if s.proc.poll() is None ]
            state.tasks_view.refresh()
            await asyncio.sleep(self.QUEUE_INTERVAL)

class State(AttrDict):

    def __init__(self, *args, **kwargs):
        super().__init__(*kwargs, **kwargs)
        self.__exclude_keys__ |= {"_procs", "procs", "task_manager"}
        self._procs = list()
        self.task_manager = TaskManager()

    @property
    def procs(self):
        return self._procs

    @procs.setter
    def procs(self, value):
        self._procs = value

    def start_task_manager(self):
        self.asyncio_loop.create_task(self.task_manager.start())


    # def spawn_play_process(self, player, **kwargs):
        # raise Exception(kwargs)
        # self.procs.append(player.play(**kwargs))
        # raise Exception(self.procs)



state = State()
store = {}
memo = Memoizer(store)
memo.regions['short'] = {'max_age': 60}
memo.regions['long'] = {'max_age': 900}



# def set_provider(p, **kwargs):

#     global provider
#     provider = providers.get(p, **kwargs)
#     session = provider.session

__all__ = ["state", "memo"]


def main():

    import time

    state.loop = asyncio.get_event_loop()
    task_manager = TaskManager()
    task_manager.add("foo")
    task_manager.add("bar")
    state.loop.create_task(task_manager.start())
    state.loop.run_until_complete(task_manager.stop())
    # state.loop.close()
    # await asyncio.sleep(10)
    # time.sleep(10)

if __name__ == "__main__":
    main()
