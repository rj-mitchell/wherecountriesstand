"""Every word a reader can be shown in the flow of a briefing: each refusal, limit and error.

The flow itself is the Where They Stand sites' shared one (wts-core, ``wts_core.briefing``), which holds no wording
of its own: these are this site's words, as they were before it, passed in whole.
"""

from __future__ import annotations

from wts_core.settings import Messages

MESSAGES = Messages(
    too_many_requests="Too many requests. Please wait a minute and try again.",
    visitor_daily_limit="You have reached today's limit for new briefings. It resets at midnight UTC; briefings "
                        "others have already asked for still load.",
    daily_cap="Today's budget for new briefings has been used up. It resets at midnight UTC.",
    monthly_cap="This month's budget for new briefings has been used up.",
    busy_visitor="A briefing is already being written for you; please wait for it to finish.",
    busy_site="Many briefings are being written at the moment; please try again in a minute.",
    sources_follow="{reason} The sources found are shown below.",
    sources_only="Sources only: no briefing was written.",
    briefing_failed="The briefing could not be written just now. The sources found are shown above; please try "
                    "again later.",
    model_declined="The model declined to answer this question.",
)
# shown in place of a briefing when there is no model to write one (no API key on this computer)
NO_MODEL = "Showing the evidence only (no Anthropic API key set)."
