#!/usr/bin/env python3
"""Agent WeChat CLI — multi-agent messaging via A2A Hub.

Usage:
  agent-wechat register --name my-agent [--type claude-code]
  agent-wechat send @bob "Hello!"
  agent-wechat send #team "PR ready"
  agent-wechat send * "Broadcast message"
  agent-wechat inbox [--json] [--ack]
  agent-wechat list [--online] [--json]
  agent-wechat status
  agent-wechat group create <name>
  agent-wechat group join <name>
  agent-wechat group list [--json]
  agent-wechat history [--with agent_name] [--json]
"""

import argparse
import asyncio
import json
import sys
import os

from hub_client import HubClient, load_config, save_config, DEFAULT_CONFIG_PATH

SCRIPT_DIR = os.path.dirname(__file__)


def _config_path() -> str:
    return os.environ.get(
        "AGENT_WECHAT_CONFIG",
        os.path.join(os.path.expanduser("~"), ".agent-wechat", "config.json"),
    )


def get_client() -> HubClient:
    cfg_path = _config_path()
    config = load_config(cfg_path)
    hub_url = config.get("hub_url", "http://localhost:9999")
    api_key = config.get("api_key", "")
    agent_id = config.get("agent_id", "")
    return HubClient(hub_url=hub_url, api_key=api_key, agent_id=agent_id)


# ── Commands ──────────────────────────────────────────────────

async def cmd_register(args):
    client = HubClient(hub_url=args.hub_url or "http://localhost:9999")
    try:
        result = await client.register(
            name=args.name,
            agent_type=args.type,
            display_name=args.display_name,
        )
        print(f"Agent '{result['agent_name']}' registered successfully!")
        print(f"Agent ID: {result['agent_id']}")
        print(f"API Key: {result['api_key']}")
        print()
        print(result['message'])

        # Save to config
        cfg_path = _config_path()
        os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
        config = load_config(cfg_path)
        config["hub_url"] = args.hub_url or config.get("hub_url", "http://localhost:9999")
        config["api_key"] = result["api_key"]
        config["agent_id"] = result["agent_id"]
        config["agent_name"] = result["agent_name"]
        config["agent_type"] = args.type
        save_config(config, cfg_path)
        print(f"\nConfiguration saved to {cfg_path}")
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Connection error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        await client.close()


async def cmd_send(args):
    client = get_client()
    if not client.api_key:
        print("Not registered. Run 'agent-wechat register' first.", file=sys.stderr)
        sys.exit(1)

    try:
        # Parse target from content prefix or use explicit arguments
        content = args.content
        target = args.target or ""
        target_type = args.target_type or "direct"

        # If content has prefix (@name:, #group:, *:) and no explicit target, parse it
        if not args.target and not args.target_type:
            parsed = _parse_prefix(content)
            if parsed:
                target_type, target, content = parsed

        if not content and not args.target and not args.target_type:
            content = args.content  # Re-send as-is, server will handle

        result = await client.send_message(
            content=content,
            target=target,
            target_type=target_type,
        )
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(f"Message sent! (id: {result.get('message_id', '?')})")
            print(f"  Delivered: {result.get('delivered_count', 0)} online")
            print(f"  Offline: {result.get('offline_count', 0)} (will deliver when they connect)")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        await client.close()


async def cmd_inbox(args):
    client = get_client()
    if not client.api_key:
        print("Not registered. Run 'agent-wechat register' first.", file=sys.stderr)
        sys.exit(1)

    try:
        messages = await client.get_inbox()
        if args.json:
            print(json.dumps({"messages": messages, "count": len(messages)}, ensure_ascii=False, indent=2))
        else:
            if not messages:
                print("No new messages.")
            else:
                print(f"=== {len(messages)} new message(s) ===\n")
                for m in messages:
                    sender = m.get("sender_name", m.get("sender_id", "unknown"))
                    target_info = ""
                    if m.get("target_type") == "group":
                        target_info = f" [#{m.get('target_id', '')}]"
                    elif m.get("target_type") == "broadcast":
                        target_info = " [广播]"
                    print(f"[{sender}{target_info}] {m.get('timestamp', '')}")
                    print(f"  {m.get('content', '')}")
                    print()

        # Auto-ack
        if args.ack and messages:
            await client.ack_inbox([m["id"] for m in messages])
            if not args.json:
                print("(messages acknowledged)")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        await client.close()


async def cmd_list(args):
    client = get_client()
    if not client.api_key:
        print("Not registered. Run 'agent-wechat register' first.", file=sys.stderr)
        sys.exit(1)

    try:
        agents = await client.list_agents(online_only=args.online)
        if args.json:
            print(json.dumps({"agents": agents}, ensure_ascii=False, indent=2))
        else:
            if not agents:
                print("No agents found.")
            else:
                status_filter = "online" if args.online else "all"
                print(f"=== Agents ({status_filter}, {len(agents)} total) ===\n")
                for a in agents:
                    status_icon = "🟢" if a.get("status") == "online" else "⚫"
                    print(f"  {status_icon} {a['name']} ({a.get('agent_type', '?')})")
                    if a.get("display_name") and a["display_name"] != a["name"]:
                        print(f"     aka: {a['display_name']}")
                    if a.get("last_seen"):
                        print(f"     last seen: {a['last_seen']}")
                    print()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        await client.close()


async def cmd_status(args):
    client = get_client()
    if not client.api_key:
        print("Not registered. Run 'agent-wechat register' first.")
        sys.exit(1)

    try:
        heartbeat = await client.heartbeat()
        me = await client.get_me()
        if args.json:
            print(json.dumps({**me, "pending_count": heartbeat.get("pending_count", 0)}, ensure_ascii=False, indent=2))
        else:
            print(f"Agent: {me.get('name', '?')}")
            print(f"Type: {me.get('agent_type', '?')}")
            print(f"Status: {me.get('status', 'offline')}")
            print(f"Hub: {client.base_url}")
            print(f"Unread messages: {heartbeat.get('pending_count', 0)}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        await client.close()


async def cmd_group(args):
    client = get_client()
    if not client.api_key:
        print("Not registered. Run 'agent-wechat register' first.", file=sys.stderr)
        sys.exit(1)

    try:
        if args.action == "create":
            result = await client.create_group(args.name, args.description or "")
            if args.json:
                print(json.dumps(result, ensure_ascii=False))
            else:
                print(result.get("message", f"Group '{args.name}' created."))

        elif args.action == "join":
            # Find group by name
            groups = await client.list_groups()
            group_id = None
            for g in groups:
                if g["name"] == args.name:
                    group_id = g["id"]
                    break
            if not group_id:
                print(f"Group '{args.name}' not found. Available groups:", file=sys.stderr)
                for g in groups:
                    print(f"  - {g['name']}", file=sys.stderr)
                sys.exit(1)
            result = await client.join_group(group_id)
            if args.json:
                print(json.dumps(result, ensure_ascii=False))
            else:
                print(result.get("message", f"Joined group '{args.name}'."))

        elif args.action == "leave":
            groups = await client.list_groups()
            group_id = None
            for g in groups:
                if g["name"] == args.name:
                    group_id = g["id"]
                    break
            if not group_id:
                print(f"Not a member of group '{args.name}'.", file=sys.stderr)
                sys.exit(1)
            result = await client.leave_group(group_id)
            print(f"Left group '{args.name}'.")

        elif args.action == "list":
            groups = await client.list_groups()
            if args.json:
                print(json.dumps({"groups": groups}, ensure_ascii=False, indent=2))
            else:
                if not groups:
                    print("No groups joined.")
                else:
                    print(f"=== Groups ({len(groups)}) ===\n")
                    for g in groups:
                        desc = f" - {g.get('description', '')}" if g.get("description") else ""
                        print(f"  # {g['name']}{desc}")
                        print(f"    id: {g['id']}")
                        print()

        elif args.action == "members":
            groups = await client.list_groups()
            group_id = None
            for g in groups:
                if g["name"] == args.name:
                    group_id = g["id"]
                    break
            if not group_id:
                print(f"Group '{args.name}' not found.", file=sys.stderr)
                sys.exit(1)
            group = await client.get_group(group_id)
            members = group.get("members", [])
            if args.json:
                print(json.dumps({"members": members}, ensure_ascii=False, indent=2))
            else:
                print(f"=== {group['name']} members ({len(members)}) ===\n")
                for m in members:
                    status_icon = "🟢" if m.get("online") else "⚫"
                    role_tag = " [admin]" if m.get("role") == "admin" else ""
                    print(f"  {status_icon} {m['agent_name']}{role_tag}")

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        await client.close()


async def cmd_history(args):
    client = get_client()
    if not client.api_key:
        print("Not registered. Run 'agent-wechat register' first.", file=sys.stderr)
        sys.exit(1)

    try:
        messages = await client.get_history(
            with_agent=args.with_agent,
            limit=args.limit,
        )
        if args.json:
            print(json.dumps({"messages": messages}, ensure_ascii=False, indent=2))
        else:
            if not messages:
                print("No message history.")
            else:
                print(f"=== History ({len(messages)} messages) ===\n")
                for m in messages:
                    sender = m.get("sender_name", "?")
                    target = m.get("target_name", m.get("target_id", "?"))
                    direction = "→"
                    print(f"[{sender} {direction} {target}] {m.get('timestamp', '')}")
                    print(f"  {m.get('content', '')}")
                    print()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        await client.close()


async def cmd_read(args):
    """Mark messages as read."""
    client = get_client()
    if not client.api_key:
        sys.exit(1)
    try:
        # Fetch inbox first if no explicit message IDs
        if args.all:
            messages = await client.get_inbox()
            if not messages:
                print("No messages to mark as read.")
                return
            msg_ids = [m["id"] for m in messages]
            await client.mark_read(msg_ids)
            print(f"Marked {len(msg_ids)} message(s) as read.")
            return

        if not args.message_ids:
            print("Usage: agent-wechat read --all  OR  agent-wechat read <id1> <id2> ...", file=sys.stderr)
            sys.exit(1)

        await client.mark_read(args.message_ids)
        print(f"Marked {len(args.message_ids)} message(s) as read.")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        await client.close()


async def cmd_sent(args):
    """Check sent message status."""
    client = get_client()
    if not client.api_key:
        sys.exit(1)
    try:
        status = await client.get_sent_status(args.message_id)
        if args.json:
            print(json.dumps(status, ensure_ascii=False, indent=2))
        else:
            icon = {"pending": "📨", "delivered": "✅", "read": "👁️"}.get(status["status"], "❓")
            print(f"{icon} Message: {status['id']}")
            print(f"   To: {status.get('target_name', '?')} ({status['target_type']})")
            print(f"   Status: {status['status']}")
            print(f"   Sent: {status['sent_at']}")
            if status.get("delivered_at"):
                print(f"   Delivered: {status['delivered_at']}")
            if status.get("read_at"):
                print(f"   Read: {status['read_at']}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        await client.close()


async def cmd_rotate_key(args):
    client = get_client()
    if not client.api_key:
        print("Not registered.", file=sys.stderr)
        sys.exit(1)

    try:
        result = await client.rotate_key()
        cfg_path = _config_path()
        config = load_config(cfg_path)
        config["api_key"] = result["api_key"]
        save_config(config, cfg_path)
        print(result.get("message", "API key rotated."))
        print(f"New key prefix: {result.get('key_prefix', '?')}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        await client.close()


# ── Helpers ───────────────────────────────────────────────────

def _parse_prefix(content: str) -> tuple[str, str, str] | None:
    """Parse @name:, #group:, or *: prefix from content.
    Returns (target_type, target_name, clean_content) or None.
    """
    content = content.strip()
    if content.startswith("*:") or content.startswith("*："):
        sep = ":" if content[1] == ":" else "："
        return ("broadcast", "*", content[2:].strip())
    if content.startswith("#"):
        for sep in (":", "："):
            idx = content.find(sep)
            if idx != -1:
                return ("group", content[1:idx].strip(), content[idx+1:].strip())
        return ("group", content[1:].strip(), "")
    if content.startswith("@"):
        for sep in (":", "："):
            idx = content.find(sep)
            if idx != -1:
                return ("direct", content[1:idx].strip(), content[idx+1:].strip())
        return ("direct", content[1:].strip(), "")
    return None


# ── Main ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Agent WeChat — multi-agent messaging via A2A Hub",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  agent-wechat register --name my-agent --type claude-code
  agent-wechat send @bob "Hello from Alice!"
  agent-wechat send "#dev-team: PR ready for review"
  agent-wechat send '*: System maintenance in 5 min'
  agent-wechat inbox --json
  agent-wechat list --online --json
  agent-wechat status
  agent-wechat group create team-alpha
  agent-wechat group join team-alpha
  agent-wechat history --with bob --json
""",
    )

    sub = parser.add_subparsers(dest="command")

    # register
    p = sub.add_parser("register", help="Register with the A2A Hub")
    p.add_argument("--name", required=True, help="Agent name (unique)")
    p.add_argument("--type", default="claude-code", help="Agent type (default: claude-code)")
    p.add_argument("--display-name", help="Optional display name")
    p.add_argument("--hub-url", default="http://localhost:9999", help="Hub URL")

    # send
    p = sub.add_parser("send", help="Send a message")
    p.add_argument("target_or_content", nargs="?", help="Target prefix + message, e.g. '@bob: hello'")
    p.add_argument("message", nargs="?", help="Message content (when not using prefix)")
    p.add_argument("--target", "-t", help="Explicit target name")
    p.add_argument("--target-type", choices=["direct", "group", "broadcast"], help="Target type")
    p.add_argument("--json", action="store_true", help="JSON output")

    # inbox
    p = sub.add_parser("inbox", help="Check incoming messages")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.add_argument("--ack", action="store_true", help="Acknowledge messages after reading")

    # list
    p = sub.add_parser("list", help="List registered agents")
    p.add_argument("--online", action="store_true", help="Only show online agents")
    p.add_argument("--json", action="store_true", help="JSON output")

    # status
    p = sub.add_parser("status", help="Show current agent status")
    p.add_argument("--json", action="store_true", help="JSON output")

    # group
    p = sub.add_parser("group", help="Group management")
    p.add_argument("action", choices=["create", "join", "leave", "list", "members"], help="Action")
    p.add_argument("name", nargs="?", help="Group name")
    p.add_argument("--description", help="Group description (for create)")
    p.add_argument("--json", action="store_true", help="JSON output")

    # history
    p = sub.add_parser("history", help="View message history")
    p.add_argument("--with", dest="with_agent", help="Filter by agent name")
    p.add_argument("--limit", type=int, default=50, help="Max messages (default: 50)")
    p.add_argument("--json", action="store_true", help="JSON output")

    # read
    p = sub.add_parser("read", help="Mark messages as read")
    p.add_argument("message_ids", nargs="*", help="Message IDs to mark as read")
    p.add_argument("--all", action="store_true", help="Mark all inbox messages as read")

    # sent
    p = sub.add_parser("sent", help="Check sent message status")
    p.add_argument("message_id", help="Message ID to check")
    p.add_argument("--json", action="store_true", help="JSON output")

    # rotate-key
    p = sub.add_parser("rotate-key", help="Rotate API key")

    # Parse
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Route to command
    command_map = {
        "register": cmd_register,
        "send": cmd_send,
        "inbox": cmd_inbox,
        "list": cmd_list,
        "status": cmd_status,
        "group": cmd_group,
        "history": cmd_history,
        "read": cmd_read,
        "sent": cmd_sent,
        "rotate-key": cmd_rotate_key,
    }

    func = command_map.get(args.command)
    if func:
        # Handle send command's flexible arguments
        if args.command == "send":
            if args.target_or_content and not args.message and not args.target:
                # Single argument: might be "message" or "@name: message" or "#group: message"
                args.content = args.target_or_content
            elif args.target_or_content and args.message:
                # Two arguments: target_or_content might be @name and message is the message
                # But this format is rarely used. Better: use the combined format
                args.content = f"{args.target_or_content} {args.message}"
            elif args.target_or_content:
                args.content = args.target_or_content
            else:
                print("Usage: agent-wechat send <@target|#group|*> <message>", file=sys.stderr)
                sys.exit(1)

        asyncio.run(func(args))
    else:
        parser.print_help()
        sys.exit(1)

    # Cleanup
    try:
        loop = asyncio.get_event_loop()
        if not loop.is_closed():
            loop.close()
    except Exception:
        pass


if __name__ == "__main__":
    main()
