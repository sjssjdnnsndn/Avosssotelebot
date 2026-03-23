"""
Robust Session Manager for Telegram bot.
Loads .session files from disk, connects them, maintains an active_clients list,
runs periodic health checks, and fires a callback when a session dies.
"""

import asyncio
import glob
import os
from datetime import datetime

from telethon import TelegramClient
from telethon.errors import AuthKeyUnregisteredError, UserDeactivatedBanError


class SessionManager:
    def __init__(
        self,
        sessions_collection,
        sessions_dir: str,
        api_id: int,
        api_hash: str,
        health_check_interval: int = 480,
        connect_stagger: float = 1.5,
        startup_wait: float = 5.0,
        on_dead_session=None,
    ):
        self.sessions_collection = sessions_collection
        self.sessions_dir = sessions_dir
        self.api_id = int(api_id)
        self.api_hash = api_hash
        self.health_check_interval = health_check_interval
        self.connect_stagger = connect_stagger
        self.startup_wait = startup_wait
        self.on_dead_session = on_dead_session

        self.active_clients: list = []
        self._running = False

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _make_client(self, session_path: str, api_id=None, api_hash=None) -> TelegramClient:
        return TelegramClient(
            session_path,
            int(api_id) if api_id else self.api_id,
            api_hash if api_hash else self.api_hash,
            connection_retries=5,
            retry_delay=3,
        )

    def _annotate(self, client: TelegramClient, phone=None, user_data=None, session_name=None):
        client._tg_phone = phone
        client._tg_user_data = user_data
        client._tg_session_name = session_name

    def _remove_from_active(self, client: TelegramClient):
        try:
            self.active_clients.remove(client)
        except ValueError:
            pass

    def _find_by_identity(self, phone=None, user_id=None):
        for c in list(self.active_clients):
            if phone and getattr(c, "_tg_phone", None) == phone:
                return c
            if user_id and getattr(c, "_tg_user_data", None) and getattr(c._tg_user_data, "id", None) == user_id:
                return c
        return None

    async def _safe_disconnect(self, client: TelegramClient):
        try:
            if client.is_connected():
                await client.disconnect()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #  Public interface                                                    #
    # ------------------------------------------------------------------ #

    async def load_all(self):
        """
        Discover all .session files in sessions_dir, connect each one,
        and populate self.active_clients.
        """
        await asyncio.sleep(self.startup_wait)

        os.makedirs(self.sessions_dir, exist_ok=True)
        session_files = sorted(glob.glob(os.path.join(self.sessions_dir, "*.session")))

        if not session_files:
            print(f"⚠️ SessionManager: no .session files found in {self.sessions_dir}")
            return

        print(f"📊 SessionManager: loading {len(session_files)} session file(s)…")
        loaded = 0
        failed = 0

        for idx, session_file in enumerate(session_files, 1):
            session_name = os.path.basename(session_file).replace(".session", "")
            session_path = os.path.join(self.sessions_dir, session_name)

            # Fetch per-session API credentials from DB if stored
            db_doc = await self.sessions_collection.find_one({"session_name": session_name})
            use_api_id = (db_doc or {}).get("api_id") or self.api_id
            use_api_hash = (db_doc or {}).get("api_hash") or self.api_hash

            client = None
            try:
                client = self._make_client(session_path, api_id=use_api_id, api_hash=use_api_hash)
                await asyncio.wait_for(client.connect(), timeout=20)

                if not await client.is_user_authorized():
                    print(f"  [{idx}] ⚠️ {session_name}: unauthorized — skipping")
                    await self.sessions_collection.update_one(
                        {"session_name": session_name},
                        {
                            "$set": {
                                "status": "unauthorized",
                                "last_error": "Unauthorized at startup",
                                "last_check": datetime.utcnow(),
                                "updated_at": datetime.utcnow(),
                            }
                        },
                    )
                    await self._safe_disconnect(client)
                    failed += 1
                    continue

                me = await client.get_me()
                phone = (me.phone or "").strip() or f"session:{session_name}"
                self._annotate(client, phone=phone, user_data=me, session_name=session_name)

                # Remove any previous duplicate for the same account
                old = self._find_by_identity(phone=phone, user_id=me.id)
                if old:
                    self._remove_from_active(old)
                    await self._safe_disconnect(old)

                self.active_clients.append(client)

                name = me.first_name or "No name"
                uname = f"@{me.username}" if me.username else "—"
                print(f"  [{idx}] ✅ {session_name}: {name} {uname} ({phone})")
                loaded += 1

            except Exception as exc:
                print(f"  [{idx}] ❌ {session_name}: {exc}")
                if client:
                    await self._safe_disconnect(client)
                failed += 1

            if idx < len(session_files):
                await asyncio.sleep(self.connect_stagger)

        self._running = True
        print(
            f"\n✅ SessionManager: {loaded} loaded, {failed} failed. "
            f"Active clients: {len(self.active_clients)}"
        )

    async def health_monitor_loop(self):
        """Periodically ping every active client; remove dead ones."""
        while self._running:
            await asyncio.sleep(self.health_check_interval)
            dead = []
            for client in list(self.active_clients):
                session_name = getattr(client, "_tg_session_name", "?")
                try:
                    if not client.is_connected():
                        await asyncio.wait_for(client.connect(), timeout=15)
                    await asyncio.wait_for(client.get_me(), timeout=15)
                except (AuthKeyUnregisteredError, UserDeactivatedBanError) as exc:
                    dead.append((client, str(exc)))
                except Exception as exc:
                    print(f"⚠️ Health check {session_name}: {exc}")

            for client, err in dead:
                session_name = getattr(client, "_tg_session_name", "?")
                print(f"💀 Dead session removed: {session_name} — {err}")
                self._remove_from_active(client)
                await self._safe_disconnect(client)

                if self.on_dead_session:
                    try:
                        await self.on_dead_session(session_name, err)
                    except Exception:
                        pass

    async def disconnect_all(self):
        """Gracefully disconnect every active client."""
        self._running = False
        for client in list(self.active_clients):
            await self._safe_disconnect(client)
        self.active_clients.clear()
        print("SessionManager: all clients disconnected.")
