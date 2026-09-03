"""
Tests for peer-targeted mention filtering in reply_gate.py and relay.py.
Ensures messages explicitly directed to other bots/users (e.g. '@Zero ...',
'@Amos ...') do not wake Marvin or invoke Tier 2 Haiku scoring.
"""

from pathlib import Path
import sys

PACKAGE_ROOT = Path(__file__).parent.parent
bin_dir = str(PACKAGE_ROOT / "bin")
if bin_dir not in sys.path:
    sys.path.insert(0, bin_dir)

from reply_gate import Decision, GateMessage, ReplyGate
from handoff import Envelope, required_but_misdirected


def test_peer_mention_drops_immediately_without_scoring():
    gate = ReplyGate(self_id="marvin_bot_id", names=("marvin",), cooldown_sec=300)
    msg = GateMessage(
        channel_id="c_agent_chat",
        author_id="user_ryan",
        content="<@1542285964213358633> do you have access to Ian's heart of gold repo",
        mentions_self=False,
        mentions_other=True,
    )
    decision = gate.evaluate(msg)
    assert not decision.wake, "Must not wake when message is targeted at another bot"
    assert not decision.needs_score, "Must skip Tier 2 scorer entirely for peer pings"
    assert decision.tier == "tier1-peer"


def test_dual_mention_wakes_marvin():
    gate = ReplyGate(self_id="marvin_bot_id", names=("marvin",), cooldown_sec=300)
    msg = GateMessage(
        channel_id="c_agent_chat",
        author_id="user_ryan",
        content="@Zero check X. @Marvin what do you think of Y?",
        mentions_self=True,
        mentions_other=True,
    )
    decision = gate.evaluate(msg)
    assert decision.wake, "Must wake when Marvin is explicitly co-addressed"
    assert decision.tier == "tier1"


def test_role_mention_with_peer_mention_wakes_marvin():
    gate = ReplyGate(self_id="marvin_bot_id", names=("marvin",), cooldown_sec=300)
    msg = GateMessage(
        channel_id="c_agent_chat",
        author_id="user_ryan",
        content="@robots @Zero check X",
        mentions_self=False,
        mentions_role=True,
        mentions_other=True,
    )
    decision = gate.evaluate(msg)
    assert decision.wake, "Must wake when shared role is pinged"
    assert decision.tier == "tier1"


def test_reply_to_self_with_peer_mention_wakes_marvin():
    gate = ReplyGate(self_id="marvin_bot_id", names=("marvin",), cooldown_sec=300)
    msg = GateMessage(
        channel_id="c_agent_chat",
        author_id="user_ryan",
        content="also cc @Zero",
        mentions_self=False,
        mentions_other=True,
        is_reply_to_self=True,
    )
    decision = gate.evaluate(msg)
    assert decision.wake, "Must wake when message is a reply to Marvin, even if tagging a peer"
    assert decision.tier == "tier1"
    assert decision.reason == "reply to you"


def test_peer_mention_with_marvin_named_in_prose_falls_through_to_scorer():
    gate = ReplyGate(self_id="marvin_bot_id", names=("marvin",), cooldown_sec=300)
    msg = GateMessage(
        channel_id="c_agent_chat",
        author_id="user_ryan",
        content="@Zero check X. Marvin, what do you think?",
        mentions_self=False,
        mentions_other=True,
    )
    decision = gate.evaluate(msg)
    assert decision.needs_score, "Must fall through to scorer when Marvin is named in prose"
    assert decision.named is True


def test_misdirected_required_handoff_declines_cleanly():
    env = Envelope(v=1, 
        kind="question",
        reply="required",
        reply_from="Amos",
        subject="banana-mutex",
    )
    assert required_but_misdirected(env, "Marvin") is True
    assert required_but_misdirected(env, "Amos") is False


if __name__ == "__main__":
    test_peer_mention_drops_immediately_without_scoring()
    test_dual_mention_wakes_marvin()
    test_role_mention_with_peer_mention_wakes_marvin()
    test_reply_to_self_with_peer_mention_wakes_marvin()
    test_peer_mention_with_marvin_named_in_prose_falls_through_to_scorer()
    test_misdirected_required_handoff_declines_cleanly()
    print("All peer filtering tests passed!")
