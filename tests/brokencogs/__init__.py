"""A fake cogs package: one healthy cog, one that explodes on import.

Used by test_one_broken_cog_does_not_take_the_bot_offline. load_cogs runs at
import time on Vercel, so a single bad file must not take the whole bot down.
"""
