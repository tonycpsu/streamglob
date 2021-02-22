import urwid
from panwid.keymap import *

from .. import config
from .. import providers
from ..widgets import *
from ..providers.base import SynchronizedPlayerMixin

class ProviderToolbar(urwid.WidgetWrap):

    signals = ["provider_change", "profile_change", "preview_change"]
    def __init__(self, default_provider):

        def format_provider(n, p):
            return p.NAME if p.config_is_valid else f"* {p.NAME}"

        def providers_sort_key(p):
            k, v = p
            # providers = list(config.settings.profile.providers.keys())
            # if k in providers:
            # raise Exception(v)
            if v.config_is_valid:
                return (0, str(v.NAME))
            else:
                return (1, str(v.NAME))

        self.provider_dropdown = BaseDropdown(AttrDict(
            [(format_provider(n, p), n)
              for n, p in sorted(
                      providers.PROVIDERS.items(),
                      key = providers_sort_key
              )]
        ) , label="Provider", default=default_provider, margin=1)

        urwid.connect_signal(
            self.provider_dropdown, "change",
            lambda w, b, v: self._emit("provider_change", v)
        )

        self.profile_dropdown = BaseDropdown(
            AttrDict(
                [ (k, k) for k in config.settings.profiles.keys()]
            ),
            label="Profile",
            default=config.settings.profile_name, margin=1
        )

        urwid.connect_signal(
            self.profile_dropdown, "change",
            lambda w, b, v: self._emit("profile_change", v)
        )


        self.preview_dropdown_placeholder = urwid.WidgetPlaceholder(urwid.Text(""))

        self.max_concurrent_tasks_widget = providers.filters.IntegerTextFilterWidget(
            default=config.settings.tasks.max,
                minimum=1
        )

        def set_max_concurrent_tasks(v):
            config.settings.tasks.max = int(v)

        self.max_concurrent_tasks_widget.connect("changed", set_max_concurrent_tasks)
        # urwid.connect_signal(
        #     self.max_concurrent_tasks_widget,
        #     "change",
        #     set_max_concurrent_tasks
        # )

        self.columns = urwid.Columns([
            # ('weight', 1, urwid.Padding(urwid.Edit("foo"))),
            (self.provider_dropdown.width, self.provider_dropdown),
            ("weight", 1, urwid.Padding(urwid.Text(""))),
            (20, self.preview_dropdown_placeholder),
            # (1, urwid.Divider(u"\N{BOX DRAWINGS LIGHT VERTICAL}")),
            ("pack", urwid.Text(("Downloads"))),
            (5, self.max_concurrent_tasks_widget),
            ("weight", 1, urwid.Padding(urwid.Text(""))),
            (self.profile_dropdown.width, self.profile_dropdown),
        ], dividechars=3)
        # self.filler = urwid.Filler(self.columns)
        super(ProviderToolbar, self).__init__(urwid.Filler(self.columns))

    def cycle_provider(self, step=1):

        self.provider_dropdown.cycle(step)

    def cycle_preview_type(self, step=1):

        self.preview_dropdown.cycle(step)

    @property
    def provider(self):
        return (self.provider_dropdown.selected_label)

    def set_preview_types(self, preview_types, provider_config):

        self.preview_dropdown = BaseDropdown(
            AttrDict([
                (pt.title(), pt)
                for pt in preview_types
                # ("Full", "full"),
                # ("Storyboard", "storyboard"),
                # ("Thumbnail", "thumbnail"),
            ]),
            label="Preview",
            default=provider_config[0].mode or "full",
            margin=1
        )

        urwid.connect_signal(
            self.preview_dropdown, "change",
            lambda w, b, v: self._emit("preview_change", v)
        )

        self.preview_dropdown_placeholder.original_widget = self.preview_dropdown


@keymapped()
class ListingsView(StreamglobView):

    KEYMAP = {
        "meta [": ("cycle_provider", [-1]),
        "meta ]": ("cycle_provider", [1]),
        "meta {": ("cycle_preview_type", [-1]),
        "meta }": ("cycle_preview_type", [1]),
    }

    SETTINGS = ["provider", "profile", "preview"]

    def __init__(self, provider):

        self.provider = provider
        self.toolbar = ProviderToolbar(self.provider.IDENTIFIER)
        urwid.connect_signal(
            self.toolbar, "provider_change",
            lambda w, p: self.on_set_provider(p)
        )

        def profile_change(p):
            config.settings.toggle_profile(p)
            player.Player.load()

        urwid.connect_signal(
            self.toolbar, "profile_change",
            lambda w, p: profile_change(p)
        )

        urwid.connect_signal(
            self.toolbar, "preview_change",
            lambda w, p: self.provider.reset()
        )

        self.listings_view_placeholder = urwid.WidgetPlaceholder(
            urwid.Filler(urwid.Text(""))
        )

        self.pile  = urwid.Pile([
            (1, self.toolbar),
            (1, urwid.Filler(urwid.Divider("-"))),
            ('weight', 1, self.listings_view_placeholder),
        ])
        super().__init__(self.pile)
        self.on_set_provider(self.provider.IDENTIFIER)

    def set_provider(self, provider):
        self.toolbar.provider_dropdown.value = provider

    @property
    def profile(self):
        return self.toolbar.profile_dropdown.value

    @profile.setter
    def profile(self, value):
        self.toolbar.profile_dropdown.value = value

    @property
    def preview(self):
        return self.toolbar.preview_dropdown.value

    @preview.setter
    def preview(self, value):
        self.toolbar.preview_dropdown.value = value

    def on_set_provider(self, provider):

        self.provider.deactivate()
        self.provider = providers.get(provider)
        self.listings_view_placeholder.original_widget = self.provider.view
        self.toolbar.set_preview_types(self.provider.PREVIEW_TYPES, self.provider.config.auto_preview)
        if self.provider.config_is_valid:
            self.pile.focus_position = 2
        else:
            self.pile.focus_position = 0
        logger.error(self.provider.default_filter_values)
        for name, value in self.provider.default_filter_values.items():
            if name not in self.SETTINGS:
                continue
            setattr(self, name, value)
        self.provider.activate()
            # value = self.provider.default_filter_values.get(name, None)
            # if value:
            #     setattr(self, name, value)

    def cycle_provider(self, step=1):
        self.toolbar.cycle_provider(step)

    def cycle_preview_type(self, step=1):
        self.toolbar.cycle_preview_type(step)

    def activate(self):
        self.set_provider(self.provider.IDENTIFIER)

    @property
    def preview_mode(self):
        return self.toolbar.preview_dropdown.value

    def on_view_activate(self):

        async def activate_async():
            logger.info("listings acivate")
            if self.provider.auto_preview_enabled:
                # self.provider.reset()
                await self.provider.view.preview_all()
                # if hasattr(state, "loop"):
                    # state.loop.draw_screen()

        state.event_loop.create_task(activate_async())

    def keypress(self, size, key):
        return super().keypress(size, key)
