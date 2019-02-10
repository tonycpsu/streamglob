import asyncio

from .player import Player, Downloader
from .state import *

task_manager_task = None

class TaskManager(object):

    QUEUE_INTERVAL = 1

    def __init__(self):

        # global state
        self.pending = asyncio.Queue()
        self.active = list()

    def add(self, *task):

        logger.info(f"adding task: {task}")
        self.pending.put_nowait(task)

    def play(self, source, player_spec, helper_spec, **kwargs):

        self.pending.put_nowait(("play", source, (player_spec, helper_spec), kwargs))

    def download(self, source, filename, helper_spec, **kwargs):

        self.pending.put_nowait(("download", source, (filename, helper_spec), kwargs))

    async def start(self):
        logger.info("task_manager starting")
        self.worker_task = state.asyncio_loop.create_task(self.worker())
        self.poller_task = state.asyncio_loop.create_task(self.poller())

    async def stop(self):
        logger.info("task_manager stopping")
        import time; time.sleep(1)
        for a in self.active:
            a.proc.terminate()

        await self.pending.join()
        self.worker_task.cancel()
        self.poller_task.cancel()
        # print(self.poller_task.exception())

    async def worker(self):

        while True:

            (action, source, args, kwargs) = await self.pending.get()
            # raise Exception
            logger.info(f"action: {action}")
            logger.info(f"{'playing' if action == 'play' else 'downloading'} source: {source}")
            if action == "play":
                program = Player.play(source, *args, **kwargs)
            elif action == "download":
                (filename, helper_spec) = args
                # proc = downloader.download(source, filename, **kwargs)
                program = Downloader.download(source, filename, helper_spec, **kwargs)
            else:
                raise NotImplementedError
            source.action = action
            source.program = program
            source.proc = program.proc
            source.pid = program.proc.pid
            logger.info(source.proc)
            logger.info(source.pid)
            source.started = datetime.now()
            source.elapsed = timedelta(0)
            self.active.append(source)
            self.pending.task_done()

    async def poller(self):

        while True:
            # logger.info("poller")
            self.active = [ s for s in self.active if s.proc.poll() is None ]
            for s in self.active:
                s.elapsed = datetime.now() - s.started
                if hasattr(s.program, "update_progress"):
                    s.program.update_progress()
            state.tasks_view.refresh()
            await asyncio.sleep(self.QUEUE_INTERVAL)


def start_task_manager():
    self.task_manager_task = state.asyncio_loop.create_task(self.task_manager.start())

def stop_task_manager(self):
    state.asyncio_loop.create_task(self.task_manager.stop())
    self.task_manager_task.cancel()


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
