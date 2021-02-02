import logging
logger = logging.getLogger(__name__)
import os
import asyncio
from datetime import datetime, timedelta
from orderedattrdict import AttrDict
import dataclasses
import itertools
import textwrap
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

BLANK_IMAGE_URI = """\
data://image/png;base64,\
iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAA\
AAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=\
"""

class TaskManager(Observable):

    QUEUE_INTERVAL = 1
    DEFAULT_MAX_CONCURRENT_TASKS = 20

    def __init__(self):

        super().__init__()
        self.preview_task = None
        self.preview_player = None
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


    def make_playlist(self, title, items):

        ITEM_TEMPLATE=textwrap.dedent(
        """\
        #EXTINF:1,{title}
        {locator}
        """)
        with tempfile.NamedTemporaryFile(suffix=".m3u8", delete=False) as m3u:
            m3u.write(f"#EXTM3U\n".encode("utf-8"))
            for item in items:
                m3u.write(ITEM_TEMPLATE.format(
                    title = item.title.strip() or "(no title)",
                    locator=item.locator
                ).encode("utf-8"))
            logger.info(m3u.name)

            # listing = self.new_listing(
            listing = AttrDict(
                # title=f"{self.provider.NAME} playlist" + (
                #     f" ({self.provider.feed.name}/"
                #     if self.provider.feed
                #     else " ("
                # ) + f"{self.provider.status})",
                title = title,
                sources = [
                    model.MediaSource.attr_class(
                        provider_id = "tasks",
                        url = f"file://{m3u.name}",
                        media_type = "video" # FIXME
                    )
                ],
            )
        return listing

    def empty_listing(self, title):
        return self.make_playlist(
            title,
            [
                AttrDict(
                    title=title,
                    locator=BLANK_IMAGE_URI,
                    media_type="image"
                )
            ]
        )


    async def preview(self, listing, caller, **kwargs):

        logger.info(listing)
        # logger.error(kwargs)
        if listing:
            logger.info(listing)
            task = caller.create_play_task(listing, **kwargs)
            logger.info(task)
        else:
            listing = self.empty_listing(caller.playlist_title)
            task = model.PlayMediaTask.attr_class(
                title = listing.title,
                sources = listing.sources
            )

        async def start_player():
            self.preview_task = task
            await self.start_task(self.preview_task)
            logger.info(self.preview_task)
            self.preview_player = await self.preview_task.program
            logger.info(self.preview_player)
            await self.preview_task.proc

            async def handle_mpv_key(key_state, key_name, key_string):
                key = self.preview_player.key_to_urwid(key_name)
                if key.startswith("mbtn"):
                    return
                logger.info(f"debug: {key_name}")
                # key = self.view.keypress((100, 100), key)
                state.loop.process_input([key])
                # if not state.loop.process_input([key]):
                # self._emit("keypress", key)

            await self.preview_player.controller.register_unbound_key_callback(
                handle_mpv_key
            )

            def on_player_done(f):
                logger.info("player done")
                self.preview_task = None
                self.preview_player = None

            self.preview_task.result.add_done_callback(on_player_done)

        async def load_sources():
            await self.preview_task.load_sources(task.sources, **kwargs)

        if self.preview_player:
            await load_sources()
        else:
            await start_player()


    def play(self, task, **kwargs):

        self.current_task_id += 1
        task.task_id = self.current_task_id
        # task.args = (player_spec, downloader_spec)
        # task.kwargs = kwargs
        # task.program = state.event_loop.create_future()
        # task.proc = state.event_loop.create_future()
        # task.result = state.event_loop.create_future()
        self.to_play.append(task)
        return task

    def download(self, task, **kwargs):

        logger.info(f"download task: {task}")
        logger.info(f"download listing: {task.listing}")
        self.current_task_id += 1
        task.task_id = self.current_task_id
        # task.args = (downloader_spec, *task.args)
        # task.kwargs = kwargs
        # task.program = state.event_loop.create_future()
        # task.proc = state.event_loop.create_future()
        # task.result = state.event_loop.create_future()
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
            asyncio.run(
                self.worker_task,
                self.poller_task
            )

    async def start_task(self, task):
        logger.debug(f"task: {task}")
        if isinstance(task, (model.PlayMediaTask, model.PlayMediaTask.attr_class)):
            run_task = player.Player.play(task, *task.args, **task.kwargs)

        elif isinstance(task, (model.DownloadMediaTask, model.DownloadMediaTask.attr_class)):
            try:
                outfile = task.stage_outfile
                run_task = player.Downloader.download(task, outfile, *task.args, **task.kwargs)
                task.stage_results.append(outfile)
            except SGFileExists as e:
                logger.warn(e)
                return
        else:
            logger.error(f"not implemented: {task}")
            raise NotImplementedError

        try:
            proc = await run_task
        except Exception as e:
            task.result.set_result(e)
            logger.error(e)
            return
        task.proc.set_result(proc)
        logger.debug(f"proc: {task.proc}")
        task.pid = proc.pid
        logger.debug(f"pid: {task.pid}")

        task.started = datetime.now()
        task.elapsed = timedelta(0)

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
            await self.start_task(task)
            if isinstance(task, model.PlayMediaTask.attr_class):
                self.playing.append(task)
            elif isinstance(task, model.DownloadMediaTask.attr_class):
                self.active.append(task)
            else:
                logger.error(type(task))
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
            logger.debug(f"finalizing {task} {type(task)} {task.__class__.mro()}")
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
                    logger.debug("postprocessor done: {task.stage_outfile}")
                    if not os.path.isfile(task.stage_outfile):
                        logger.warn(f"processing stage {task.stage} failed")
                        task.postprocessors = []
                    task.stage_results.append(task.stage_outfile)
                    task.postprocessors.pop(0)
                    if len(task.postprocessors):
                        task.reset()
                else:
                    logger.debug(f"postprocessor still running: {task.program}")
            elif len(task.postprocessors) > 0:
                pp = task.postprocessors[0]

                logger.info(task.listing)
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
