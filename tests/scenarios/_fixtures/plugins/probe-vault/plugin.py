"""The fixture plugin's Python half: a loadable `Plugin` and nothing else.

It owns no tool and reads no settings — the scenarios are about the *surface* an installed plugin gets (a panel,
a settings tab, a client module served over HTTP), and a kernel-lane assertion about tool behaviour would need a
recorded provider stream for a tool that does not exist yet. The class only has to survive `setup`/`teardown`.
"""


class ProbeVaultPlugin:
    def __init__(self, manifest=None) -> None:
        self.manifest = manifest
        self.ctx = None

    async def setup(self, ctx) -> None:
        self.ctx = ctx

    async def teardown(self) -> None:
        self.ctx = None
