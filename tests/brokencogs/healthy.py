"""A perfectly ordinary cog. It must still load when its neighbour is broken."""

from bot.core import Cog, command


class Healthy(Cog):
    name = "Healthy"

    @command("ping")
    def ping(self, ctx):
        ctx.reply("pong")


def setup(bot):
    bot.add_cog(Healthy(bot))
