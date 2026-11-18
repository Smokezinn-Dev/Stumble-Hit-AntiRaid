# ============================================================
# COMMANDS_PROTECTION_TOGGLE.PY - v1.0
# CONTROLE TOTAL DA PROTEÇÃO CONTRA USUÁRIOS
# ============================================================
# 📌 O QUE ESSE ARQUIVO FAZ?
# Permite admin/owner desativar GLOBALMENTE os sistemas de
# proteção contra USUÁRIOS (anti-nuke user, anti-escalation,
# automod).
#
# ⚠️ IMPORTANTE: Bots SEMPRE continuam protegidos. Isso aqui só
# afeta a proteção contra USER (gente normal).
#
# Modos:
#   - "manual": só volta quando admin reativar
#   - "auto":   volta automaticamente após X minutos
#
# Toda ação é logada + notificada para o owner.
# ============================================================

import discord
from discord.ext import commands, tasks
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional

# Motor: operações de banco aguardadas para não bloquear o event loop do Discord.
from database import get_connection_async
from embeds import embed_success, embed_error, embed_warning, embed_info
from utils import TTLCache


def _now():
    """Hora atual em UTC."""
    return datetime.now(timezone.utc)


# ============================================================
# CONFIGURAÇÃO PADRÃO
# ============================================================

DEFAULT_CONFIG = {
    "enabled": True,
    "mode": "auto",              # "manual" ou "auto"
    "auto_duration_minutes": 60, # usado quando mode = "auto"
    "notify_owner": True,
    "log_channel": 0,
}

# Módulos que podem ser desativados (todos contra users).
TOGGLEABLE_MODULES = {
    "antinuke":        "🚨 Anti-Nuke (contra usuários)",
    "antiescalation":  "🔒 Anti-Escalation (contra usuários)",
    "automod":         "🤖 AutoMod (contra usuários)",
}


# ============================================================
# MANAGER
# ============================================================

class ProtectionToggleManager:
    _cache = TTLCache(max_size=200, ttl=60)

    @classmethod
    async def get_config(cls, guild_id: int) -> dict:
        """Pega config (cache OU banco), sem bloquear o event loop.

        O cache continua sendo consultado primeiro; somente um cache miss
        executa a leitura Motor, e o resultado mantém o merge padrão original.
        """
        cached = cls._cache.get(guild_id)
        if cached is not None:
            return cached
        db = await get_connection_async()
        doc = await db["protection_toggle_config"].find_one(
            {"guild_id": guild_id}) or {}
        cfg = {**DEFAULT_CONFIG, **doc}
        cls._cache.set(guild_id, cfg)
        return cfg

    @classmethod
    async def update(cls, guild_id: int, key: str, value):
        """Persiste a configuração antes de invalidar o cache local."""
        db = await get_connection_async()
        await db["protection_toggle_config"].update_one(
            {"guild_id": guild_id}, {"$set": {key: value}}, upsert=True)
        cls._cache.invalidate(guild_id)

    @classmethod
    def invalidate(cls, guild_id: int):
        cls._cache.invalidate(guild_id)


# ============================================================
# COG
# ============================================================

class ProtectionToggleCommands(commands.Cog):
    """
    Comandos:
        >protecao                → painel
        >protecao off <modulo>   → desativa
        >protecao on <modulo>    → reativa
        >protecao offall         → desativa tudo
        >protecao onall          → reativa tudo
        >protecao status         → ver estado
        >protecao mode manual/auto
        >protecao duration <min>
        >protecao log #canal
    """

    def __init__(self, bot):
        self.bot = bot
        # Estrutura: {guild_id: {module: {"expires_at": dt|None, "user_id": int}}}
        self._active: Dict[int, Dict[str, dict]] = {}
        # Inicia task de auto-restore.
        self._auto_restore.start()

    def cog_unload(self):
        """Cancela a task ao descarregar."""
        try:
            self._auto_restore.cancel()
        except Exception:
            pass

    # ============================================================
    # AUTO RESTORE (task que roda a cada minuto)
    # ============================================================

    @tasks.loop(minutes=1)
    async def _auto_restore(self):
        """Verifica se algum módulo expirou e reativa."""
        now = _now()
        for guild_id in list(self._active.keys()):
            toggles = self._active[guild_id]
            for module in list(toggles.keys()):
                info = toggles[module]
                expires = info.get("expires_at")
                # Se expirou, restaura.
                if expires and now >= expires:
                    restored = await self._restore_module(
                        guild_id, module, reason="Auto-restore")
                    if restored:
                        toggles.pop(module, None)
            # Limpa guild se não tem mais nada.
            if not toggles:
                self._active.pop(guild_id, None)

    @_auto_restore.before_loop
    async def _before_auto(self):
        await self.bot.wait_until_ready()
        # Restaura os desligamentos temporários/manuais após reinício.
        try:
            # A conexão Motor é assíncrona; find() cria um cursor que deve
            # ser percorrido com async for, sem materializar tudo em memória.
            db = await get_connection_async()
            async for doc in db["active_protection_toggles"].find({}):
                guild_id = int(doc.get("guild_id", 0))
                module = doc.get("module")
                if not guild_id or module not in TOGGLEABLE_MODULES:
                    continue
                expires = doc.get("expires_at")
                if expires and expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                self._active.setdefault(guild_id, {})[module] = {
                    "user_id": int(doc.get("user_id", 0)),
                    "expires_at": expires,
                }
                # Reaplica o estado persistido caso o processo tenha caído
                # entre a gravação do estado e a atualização da configuração.
                if expires is None or expires > _now():
                    if module == "antinuke":
                        from commands_antinuke import AntiNukeManager
                        AntiNukeManager.update(guild_id, "user_protection_enabled", False)
                        AntiNukeManager.invalidate(guild_id)
                    elif module == "antiescalation":
                        from commands_antiescalation import AntiEscalationManager
                        await AntiEscalationManager.update(guild_id, "user_protection_enabled", False)
                        await AntiEscalationManager.invalidate(guild_id)
                    elif module == "automod":
                        from commands_automod import AutomodManager
                        await AutomodManager.update(guild_id, "user_protection_enabled", False)
        except Exception as exc:
            # Não declara restauração concluída quando o banco falha.
            print(f"[ProtectionToggle] Falha ao restaurar estado: {exc}")

    # ============================================================
    # APLICAR / RESTAURAR
    # ============================================================

    async def _disable_module(self, guild_id: int, module: str,
                                user_id: int, expires_at: Optional[datetime]):
        """Desativa e persiste o estado antes da alteração efetiva."""
        if module not in TOGGLEABLE_MODULES:
            return
        # Se o banco não aceitar a persistência, não desligamos a proteção:
        # evita um estado desativado que ficaria esquecido após reinício.
        # Primeiro grava o estado durável. Se o MongoDB falhar, a proteção
        # não será desativada e não ficará desligada após um reinício.
        db = await get_connection_async()
        await db["active_protection_toggles"].update_one(
            {"guild_id": guild_id, "module": module},
            {"$set": {"guild_id": guild_id, "module": module,
                      "user_id": user_id, "expires_at": expires_at,
                      "updated_at": _now()}}, upsert=True)

        if module == "antinuke":
            from commands_antinuke import AntiNukeManager
            AntiNukeManager.update(guild_id, "user_protection_enabled", False)
            AntiNukeManager.invalidate(guild_id)
        elif module == "antiescalation":
            from commands_antiescalation import AntiEscalationManager
            await AntiEscalationManager.update(guild_id, "user_protection_enabled", False)
            await AntiEscalationManager.invalidate(guild_id)
        elif module == "automod":
            from commands_automod import AutomodManager
            await AutomodManager.update(guild_id, "user_protection_enabled", False)

        self._active.setdefault(guild_id, {})[module] = {
            "user_id": user_id, "expires_at": expires_at,
        }
        await db["protection_toggle_logs"].insert_one({
            "guild_id": guild_id, "module": module, "enabled": False,
            "user_id": user_id, "expires_at": expires_at,
            "timestamp": _now(),
        })

    async def _restore_module(self, guild_id: int, module: str,
                                reason: str = "Manual"):
        """Reativa um módulo de proteção."""
        try:
            # Reativa cada módulo.
            if module == "antinuke":
                from commands_antinuke import AntiNukeManager
                AntiNukeManager.update(guild_id, "user_protection_enabled", True)
                AntiNukeManager.invalidate(guild_id)
            elif module == "antiescalation":
                from commands_antiescalation import AntiEscalationManager
                await AntiEscalationManager.update(guild_id, "user_protection_enabled", True)
                await AntiEscalationManager.invalidate(guild_id)
            elif module == "automod":
                from commands_automod import AutomodManager
                await AutomodManager.update(guild_id, "user_protection_enabled", True)

            # Só apaga o registro de desligamento depois que a proteção foi
            # reativada; assim uma falha mantém o estado recuperável.
            try:
                db = await get_connection_async()
                # O delete e o log são aguardados para confirmar a persistência
                # antes de informar que a restauração terminou.
                await db["active_protection_toggles"].delete_one(
                    {"guild_id": guild_id, "module": module})
                await db["protection_toggle_logs"].insert_one({
                    "guild_id": guild_id,
                    "module": module,
                    "enabled": True,
                    "user_id": 0,
                    "reason": reason,
                    "timestamp": _now(),
                })
            except Exception:
                return False
            return True
        except Exception:
            return False

    # ============================================================
    # LOG / NOTIFY
    # ============================================================

    async def _log(self, guild: discord.Guild, module: str,
                    enabled: bool, user: discord.Member,
                    expires_at: Optional[datetime] = None):
        """Envia log + notifica owner."""
        cfg = await ProtectionToggleManager.get_config(guild.id)

        # 1. Canal de log.
        log_ch = cfg.get("log_channel")
        if log_ch:
            channel = guild.get_channel(int(log_ch))
            if channel:
                try:
                    if enabled:
                        # Reativado.
                        embed = discord.Embed(
                            title="Proteção reativada",
                            description=(
                                f"O sistema **{TOGGLEABLE_MODULES.get(module, module)}** "
                                f"foi reativado."
                            ),
                            color=discord.Color.green(),
                            timestamp=_now())
                        embed.add_field(name="Reativado por",
                                        value=user.mention if user else "Sistema",
                                        inline=True)
                    else:
                        # Desativado.
                        embed = discord.Embed(
                            title="Proteção desativada",
                            description=(
                                f"O sistema **{TOGGLEABLE_MODULES.get(module, module)}** "
                                f"foi desativado.\n"
                                f"**Isso afeta todos os usuários.**"
                            ),
                            color=discord.Color.orange(),
                            timestamp=_now())
                        embed.add_field(name="Desativado por",
                                        value=user.mention if user else "?",
                                        inline=True)
                        if expires_at:
                            embed.add_field(
                                name="Reativa em",
                                value=f"<t:{int(expires_at.timestamp())}:R>",
                                inline=True)
                        else:
                            embed.add_field(name="Modo",
                                            value="Manual (até reativar)",
                                            inline=True)
                    await channel.send(embed=embed)
                except Exception:
                    pass

        # 2. Notifica o owner via DM (só quando DESATIVA).
        if cfg.get("notify_owner") and not enabled:
            try:
                owner = guild.owner or await guild.fetch_member(guild.owner_id)
                if owner:
                    await owner.send(embed=discord.Embed(
                        title="⚠️ Proteção desativada em " + guild.name,
                        description=(
                            f"**Módulo:** {TOGGLEABLE_MODULES.get(module, module)}\n"
                            f"**Por:** {user.mention if user else '?'}\n"
                            f"**Reativa:** "
                            + (f"<t:{int(expires_at.timestamp())}:R>"
                               if expires_at else "manualmente"),
                        ),
                        color=discord.Color.orange(),
                        timestamp=_now()))
            except Exception:
                pass

    # ============================================================
    # COMANDOS
    # ============================================================

    @commands.group(name="protecao", aliases=["protection", "prot"],
                    invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def protecao(self, ctx):
        """Painel de controle de proteção."""
        cfg = await ProtectionToggleManager.get_config(ctx.guild.id)
        active = self._active.get(ctx.guild.id, {})

        embed = discord.Embed(
            title="Proteção de usuários",
            description=(
                "Controle os módulos que atuam sobre membros.\n"
                "As proteções específicas para bots continuam ativas."
            ),
            color=discord.Color.blue(),
            timestamp=_now())

        # Monta linhas de status.
        lines = []
        for key, label in TOGGLEABLE_MODULES.items():
            info = active.get(key)
            if info:
                exp = info.get("expires_at")
                if exp:
                    lines.append(
                        f"• {label}: 🔴 **OFF** (expira <t:{int(exp.timestamp())}:R>)")
                else:
                    lines.append(f"• {label}: 🔴 **OFF** (manual)")
            else:
                lines.append(f"• {label}: 🟢 ON")

        embed.add_field(name="Status", value="\n".join(lines), inline=False)
        embed.add_field(
            name="Modo Atual",
            value=f"`{cfg.get('mode')}` "
                  f"(duração auto: `{cfg.get('auto_duration_minutes')}min`)",
            inline=False)
        embed.add_field(
            name="Comandos",
            value=(
                "`>protecao off <modulo>` — desativar\n"
                "`>protecao on <modulo>` — reativar\n"
                "`>protecao offall` — desativar tudo\n"
                "`>protecao onall` — reativar tudo\n"
                "`>protecao mode manual/auto`\n"
                "`>protecao duration <min>`\n"
                "`>protecao log #canal`"
            ),
            inline=False)

        await ctx.send(embed=embed)

    @protecao.command(name="off")
    @commands.has_permissions(administrator=True)
    async def protecao_off(self, ctx, module: str):
        """Desativa um módulo."""
        module = module.lower()
        if module not in TOGGLEABLE_MODULES:
            return await ctx.send(embed=embed_error(
                f"❌ Módulo inválido. Use: {', '.join(TOGGLEABLE_MODULES)}"))

        cfg = await ProtectionToggleManager.get_config(ctx.guild.id)
        # Se modo auto, define expiração.
        expires_at = None
        if cfg.get("mode") == "auto":
            expires_at = _now() + timedelta(
                minutes=int(cfg.get("auto_duration_minutes", 60)))

        await self._disable_module(ctx.guild.id, module,
                                     ctx.author.id, expires_at)
        await self._log(ctx.guild, module, False, ctx.author, expires_at)

        msg = f"✅ Módulo **{module}** desativado."
        if expires_at:
            msg += f"\nReativa <t:{int(expires_at.timestamp())}:R>."
        else:
            msg += "\nModo **manual** — use `>protecao on` para reativar."
        await ctx.send(embed=embed_success(msg))

    @protecao.command(name="on")
    @commands.has_permissions(administrator=True)
    async def protecao_on(self, ctx, module: str):
        """Reativa um módulo."""
        module = module.lower()
        if module not in TOGGLEABLE_MODULES:
            return await ctx.send(embed=embed_error(
                f"❌ Módulo inválido. Use: {', '.join(TOGGLEABLE_MODULES)}"))

        active = self._active.get(ctx.guild.id, {})
        if module not in active:
            return await ctx.send(embed=embed_warning(
                f"⚠️ Módulo `{module}` já está ativo."))

        await self._restore_module(ctx.guild.id, module,
                                     reason=f"Reativado por {ctx.author}")
        active.pop(module, None)
        await self._log(ctx.guild, module, True, ctx.author)

        await ctx.send(embed=embed_success(
            f"✅ Módulo **{module}** reativado!"))

    @protecao.command(name="offall")
    @commands.has_permissions(administrator=True)
    async def protecao_offall(self, ctx):
        """Desativa TODOS os módulos."""
        cfg = await ProtectionToggleManager.get_config(ctx.guild.id)
        expires_at = None
        if cfg.get("mode") == "auto":
            expires_at = _now() + timedelta(
                minutes=int(cfg.get("auto_duration_minutes", 60)))

        # Desativa cada um.
        for module in TOGGLEABLE_MODULES:
            await self._disable_module(ctx.guild.id, module,
                                         ctx.author.id, expires_at)
            await self._log(ctx.guild, module, False, ctx.author, expires_at)

        msg = "✅ **TODOS** os módulos de proteção contra usuários foram desativados."
        if expires_at:
            msg += f"\nReativam <t:{int(expires_at.timestamp())}:R>."
        await ctx.send(embed=embed_success(msg))

    @protecao.command(name="onall")
    @commands.has_permissions(administrator=True)
    async def protecao_onall(self, ctx):
        """Reativa TODOS os módulos."""
        active = self._active.get(ctx.guild.id, {})
        if not active:
            return await ctx.send(embed=embed_warning(
                "⚠️ Nenhum módulo desativado."))

        for module in list(active.keys()):
            await self._restore_module(ctx.guild.id, module, "Manual")
            active.pop(module, None)
            await self._log(ctx.guild, module, True, ctx.author)

        await ctx.send(embed=embed_success(
            "✅ **TODOS** os módulos reativados!"))

    @protecao.command(name="status")
    @commands.has_permissions(administrator=True)
    async def protecao_status(self, ctx):
        """Mostra quais módulos estão desativados."""
        active = self._active.get(ctx.guild.id, {})
        if not active:
            return await ctx.send(embed=embed_info(
                "🟢 Todos os sistemas de proteção estão **ATIVOS**."))

        embed = discord.Embed(
            title="Módulos desativados",
            color=discord.Color.orange(),
            timestamp=_now())
        for module, info in active.items():
            exp = info.get("expires_at")
            embed.add_field(
                name=TOGGLEABLE_MODULES.get(module, module),
                value=(
                    f"**Por:** <@{info['user_id']}>\n"
                    + (f"**Expira:** <t:{int(exp.timestamp())}:R>"
                       if exp else "**Modo:** manual")
                ),
                inline=False)
        await ctx.send(embed=embed)

    @protecao.command(name="mode")
    @commands.has_permissions(administrator=True)
    async def protecao_mode(self, ctx, mode: str):
        """Define o modo (manual/auto)."""
        mode = mode.lower()
        if mode not in ("manual", "auto"):
            return await ctx.send(embed=embed_error(
                "❌ Use: `manual` ou `auto`"))
        await ProtectionToggleManager.update(ctx.guild.id, "mode", mode)
        await ctx.send(embed=embed_success(f"✅ Modo: `{mode}`"))

    @protecao.command(name="duration")
    @commands.has_permissions(administrator=True)
    async def protecao_duration(self, ctx, minutes: int):
        """Define a duração do modo auto (em minutos)."""
        if minutes < 1 or minutes > 1440:
            return await ctx.send(embed=embed_error(
                "❌ Entre 1 e 1440 minutos."))
        await ProtectionToggleManager.update(ctx.guild.id, "auto_duration_minutes",
                                        minutes)
        await ctx.send(embed=embed_success(
            f"✅ Duração auto: `{minutes}min`"))

    @protecao.command(name="log")
    @commands.has_permissions(administrator=True)
    async def protecao_log(self, ctx, channel: discord.TextChannel):
        """Define o canal de log."""
        await ProtectionToggleManager.update(ctx.guild.id, "log_channel", channel.id)
        await ctx.send(embed=embed_success(f"✅ Log em {channel.mention}"))

    @protecao.command(name="notify")
    @commands.has_permissions(administrator=True)
    async def protecao_notify(self, ctx, state: str):
        """Liga/desliga notificação ao owner."""
        enabled = state.lower() in ("on", "true", "1", "sim")
        await ProtectionToggleManager.update(ctx.guild.id, "notify_owner", enabled)
        await ctx.send(embed=embed_success(
            f"✅ Notificar owner: {'ON' if enabled else 'OFF'}"))


async def setup(bot):
    """Chamado pelo main.py."""
    if bot.get_cog("ProtectionToggleCommands") is None:
        await bot.add_cog(ProtectionToggleCommands(bot))