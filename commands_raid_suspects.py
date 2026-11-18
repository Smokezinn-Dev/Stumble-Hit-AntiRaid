# ============================================================
# COMMANDS_RAID_SUSPECTS.PY - v1.0
# DETECÇÃO DE RAID COM LISTA DE SUSPEITOS
# ============================================================
# 📌 O QUE ESSE ARQUIVO FAZ?
# Rastreia mensagens "suspeitas" (prefixo + caractere especial
# ou palavras-chave tipo "nuke", "raid", "start"). Se uma raid
# acontecer, gera uma LISTA de suspeitos com quem mandou essas
# mensagens nos últimos minutos.
#
# RECURSOS:
# - Detecta comandos suspeitos (prefixo+chars ou keywords)
# - Cache de mensagens suspeitas (janela configurável)
# - Detecta raid (via antinuke, mass join, etc)
# - Lista de suspeitos com mensagem que enviaram
# - Principal suspeito (quem adicionou bot malicioso)
# - Palavras-chave 100% culpado (nuke, raid, start)
# - Log rico com tudo
# ============================================================

import re
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import discord
from discord.ext import commands

from database import get_connection_async
from embeds import embed_success, embed_error, embed_warning, embed_info
from embeds_professional import ProfessionalLog, fmt_reason
from utils import TTLCache


def _now() -> datetime:
    """Hora atual em UTC."""
    return datetime.now(timezone.utc)


# ============================================================
# CONFIGURAÇÃO PADRÃO
# ============================================================

DEFAULT_CONFIG = {
    "enabled": True,

    # Palavras-chave que marcam 100% de culpa.
    "palavras_chave_cem_por_cento": [
        "nuke", "raid", "atacar", "destruir", "crash",
        "wipe", "start", "hack", "exploit", "ddos",
    ],

    # Prefixos aceitos (o bot pega o prefixo atual + esses).
    "prefixos_extras": [">", "!", ".", "?", "-", "+", "$", "%", "&"],

    # Janela de suspeita (minutos antes da raid).
    "janela_suspeita_min": 10,

    # Tempo de retenção das mensagens suspeitas (minutos).
    "retencao_min": 60,

    # Notificação.
    "canal_raid_log": 0,          # se 0, usa log do antinuke
    "notificar_owner": True,
    "mencionar_suspeitos": True,
    "mostrar_mensagem": True,

    # Auto-detecção de raid.
    "auto_detectar_raid": True,
    "raid_join_threshold": 5,      # N membros em X segundos
    "raid_join_window": 10,
}


# ============================================================
# MANAGER
# ============================================================

class RaidSuspectsManager:
    _cache = TTLCache(max_size=200, ttl=60)

    @classmethod
    async def get_config(cls, guild_id: int) -> dict:
        """Pega config do cache ou consulta o MongoDB sem bloquear o bot."""
        cached = cls._cache.get(guild_id)
        if cached is not None:
            return cached
        # Usa Motor para que a leitura não bloqueie o event loop do Discord.
        db = await get_connection_async()
        doc = await db["raid_suspects_config"].find_one(
            {"guild_id": guild_id}) or {}
        cfg = {**DEFAULT_CONFIG, **doc}
        cls._cache.set(guild_id, cfg)
        return cfg

    @classmethod
    async def update(cls, guild_id: int, key: str, value):
        """Persiste a configuração antes de invalidar o cache local."""
        db = await get_connection_async()
        # Aguarda a gravação para não anunciar uma alteração ainda não salva.
        await db["raid_suspects_config"].update_one(
            {"guild_id": guild_id}, {"$set": {key: value}}, upsert=True)
        # A invalidação ocorre somente depois da confirmação do MongoDB.
        cls._cache.invalidate(guild_id)

    @classmethod
    def invalidate(cls, guild_id: int):
        cls._cache.invalidate(guild_id)


# ============================================================
# COG
# ============================================================

class RaidSuspectsCommands(commands.Cog):
    """Detecção de raid com rastreio de suspeitos."""

    def __init__(self, bot):
        self.bot = bot

        # Cache de mensagens suspeitas em memória.
        # {guild_id: deque[(user_id, username, msg_content, timestamp)]}
        self._suspeitas: Dict[int, deque] = defaultdict(
            lambda: deque(maxlen=500))

        # Rastreio de joins (pra detectar mass join).
        # {guild_id: deque[timestamp]}
        self._joins: Dict[int, deque] = defaultdict(
            lambda: deque(maxlen=100))

    # ============================================================
    # DETECTAR SUSPEITA EM MENSAGENS
    # ============================================================

    def _is_suspeita(self, content: str, cfg: dict) -> Tuple[bool, str]:
        """
        Verifica se uma mensagem é suspeita.
        Retorna (é_suspeita, motivo).
        Motivo: "palavra_chave", "prefixo_char", "nao".
        """
        if not content:
            return False, "nao"

        content_lower = content.lower().strip()
        if len(content_lower) < 2:
            return False, "nao"

        # ─── Verifica palavra-chave (100% culpado). ───
        keywords = cfg.get("palavras_chave_cem_por_cento", [])
        for kw in keywords:
            # Procura ">kw" ou "!kw" etc.
            for prefixo in cfg.get("prefixos_extras", []):
                padrao = f"{prefixo}{kw}"
                if content_lower.startswith(padrao):
                    return True, "palavra_chave"
            # Ou keyword solta tipo "> nuke".
            if kw in content_lower.split():
                return True, "palavra_chave"

        # ─── Verifica prefixo + caractere especial. ───
        prefixos = cfg.get("prefixos_extras", [])
        for prefixo in prefixos:
            if content_lower.startswith(prefixo):
                resto = content_lower[len(prefixo):].strip()
                if not resto:
                    continue
                # Se começa com caractere especial não-alfanumérico.
                primeiro = resto[0]
                if not primeiro.isalnum():
                    return True, "prefixo_char"
                # Ou se é muito curto (1-5 chars) + char especial.
                if len(resto) <= 5 and any(
                        not c.isalnum() and not c.isspace()
                        for c in resto):
                    return True, "prefixo_char"

        return False, "nao"

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Analisa toda mensagem em busca de suspeitas."""
        if not message.guild or message.author.bot:
            return

        cfg = await RaidSuspectsManager.get_config(message.guild.id)
        if not cfg.get("enabled"):
            return

        content = message.content or ""
        suspeita, motivo = self._is_suspeita(content, cfg)
        if not suspeita:
            return

        # Registra na memória.
        self._suspeitas[message.guild.id].append({
            "user_id": message.author.id,
            "username": str(message.author),
            "content": content[:200],
            "timestamp": time.time(),
            "motivo": motivo,
            "canal_id": message.channel.id,
            "msg_id": message.id,
        })

        # Persiste no DB (pra sobreviver a restart).
        try:
            # Persiste a mensagem suspeita para que a análise sobreviva a restart.
            db = await get_connection_async()
            await db["raid_suspects_log"].insert_one({
                "guild_id": message.guild.id,
                "user_id": message.author.id,
                "username": str(message.author),
                "content": content[:200],
                "motivo": motivo,
                "canal_id": message.channel.id,
                "msg_id": message.id,
                "timestamp": _now(),
            })
        except Exception:
            pass

    # ============================================================
    # DETECTAR RAID (via mass join)
    # ============================================================

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Chamado quando alguém entra. Detecta mass join."""
        if member.bot:
            return

        cfg = await RaidSuspectsManager.get_config(member.guild.id)
        if not cfg.get("enabled") or not cfg.get("auto_detectar_raid"):
            return

        now = time.time()
        dq = self._joins[member.guild.id]
        dq.append(now)

        janela = float(cfg.get("raid_join_window", 10))
        limite = int(cfg.get("raid_join_threshold", 5))

        # Limpa entradas antigas.
        while dq and now - dq[0] > janela:
            dq.popleft()

        # Se passou do limite, é raid!
        if len(dq) >= limite:
            await self._processar_raid(
                member.guild,
                motivo=f"Mass join ({len(dq)} membros em {int(janela)}s)",
                tipo="mass_join",
            )
            # Limpa pra evitar spam.
            dq.clear()

    async def _processar_raid(self, guild: discord.Guild,
                                motivo: str, tipo: str,
                                executor: Optional[discord.Member] = None):
        """
        Processa uma detecção de raid.
        Busca suspeitos e envia log.
        """
        cfg = await RaidSuspectsManager.get_config(guild.id)

        # Busca suspeitos na janela.
        suspeitos = await self._listar_suspeitos(guild, cfg)

        # Descobre principal suspeito (quem adicionou bot recente).
        principal = await self._achar_principal_suspeito(guild)

        # Envia log.
        await self._enviar_log_raid(
            guild, motivo, tipo, suspeitos, principal, executor)

    async def _listar_suspeitos(self, guild: discord.Guild,
                                  cfg: dict) -> List[dict]:
        """
        Retorna lista de suspeitos na janela configurada.
        Cada item: {user, content, timestamp, motivo, status}
        status: "cem_por_cento" (palavra-chave), "suspeito" (prefixo+char)
        """
        agora = time.time()
        janela = float(cfg.get("janela_suspeita_min", 10)) * 60
        corte = agora - janela

        # ─── Do cache em memória. ───
        suspeitos_memoria = []
        for s in self._suspeitas.get(guild.id, []):
            if s["timestamp"] < corte:
                continue
            user = guild.get_member(s["user_id"])
            if not user:
                continue

            status = ("cem_por_cento"
                       if s["motivo"] == "palavra_chave"
                       else "suspeito")
            suspeitos_memoria.append({
                "user": user,
                "content": s["content"],
                "timestamp": s["timestamp"],
                "motivo": s["motivo"],
                "status": status,
                "canal_id": s.get("canal_id"),
            })

        # ─── Do DB (caso o bot tenha reiniciado). ───
        if len(suspeitos_memoria) < 5:
            try:
                # Recupera suspeitos persistidos caso a memória tenha sido perdida
                # em um reinício. O limite e a ordem originais são preservados.
                db = await get_connection_async()
                cutoff_dt = _now() - __import__("datetime").timedelta(
                    minutes=int(cfg.get("janela_suspeita_min", 10)))
                docs = await db["raid_suspects_log"].find({
                    "guild_id": guild.id,
                    "timestamp": {"$gte": cutoff_dt},
                }).sort("timestamp", -1).limit(30).to_list(length=30)

                # Marca quem já vimos.
                vistos = {s["user"].id for s in suspeitos_memoria}
                for d in docs:
                    if d["user_id"] in vistos:
                        continue
                    user = guild.get_member(d["user_id"])
                    if not user:
                        continue
                    status = ("cem_por_cento"
                               if d.get("motivo") == "palavra_chave"
                               else "suspeito")
                    suspeitos_memoria.append({
                        "user": user,
                        "content": d.get("content", ""),
                        "timestamp": d["timestamp"].timestamp()
                                       if hasattr(d["timestamp"], "timestamp")
                                       else time.time(),
                        "motivo": d.get("motivo", "?"),
                        "status": status,
                        "canal_id": d.get("canal_id"),
                    })
                    vistos.add(d["user_id"])
            except Exception:
                pass

        # Ordena: cem_por_cento primeiro, mais recente antes.
        suspeitos_memoria.sort(
            key=lambda s: (s["status"] != "cem_por_cento",
                            -s["timestamp"]))
        return suspeitos_memoria

    async def _achar_principal_suspeito(
        self, guild: discord.Guild) -> Optional[dict]:
        """
        Procura quem adicionou o bot mais recente (potencial culpado).
        Retorna: {user, bot_name, bot_id, quando} ou None.
        """
        try:
            async for entry in guild.audit_logs(
                    limit=5,
                    action=discord.AuditLogAction.bot_add):
                # Só considera se foi nos últimos 30 min.
                delta = (discord.utils.utcnow()
                         - entry.created_at).total_seconds()
                if delta > 1800:
                    break
                executor = guild.get_member(entry.user_id)
                bot_member = entry.target
                if executor:
                    return {
                        "user": executor,
                        "bot_name": str(bot_member),
                        "bot_id": bot_member.id,
                        "quando": entry.created_at,
                    }
        except Exception:
            pass
        return None

    async def _enviar_log_raid(self, guild: discord.Guild, motivo: str,
                                 tipo: str, suspeitos: List[dict],
                                 principal: Optional[dict],
                                 executor: Optional[discord.Member]):
        """Envia log rico de raid detectada."""
        cfg = await RaidSuspectsManager.get_config(guild.id)

        # Decide canal.
        canal_id = cfg.get("canal_raid_log")
        if not canal_id:
            # Fallback pro antinuke.
            try:
                from commands_antinuke import AntiNukeManager
                an_cfg = AntiNukeManager.get_config(guild.id)
                canal_id = an_cfg.get("log_channel")
            except Exception:
                pass

        if not canal_id:
            return
        canal = guild.get_channel(int(canal_id))
        if not canal:
            return

        # ─── Monta embed. ───
        log = ProfessionalLog(
            title="🚨 RAID DETECTADA",
            description=(
                f"O sistema detectou uma raid em andamento. "
                f"Analise a lista de suspeitos abaixo."
            ),
            severity="critical",
            footer="Stumble Hit AntiRaid • Detecção de Raid")

        log.add_field("Motivo", motivo, inline=False)
        log.add_field("Tipo", f"`{tipo}`", inline=True)
        log.add_field("Suspeitos", f"`{len(suspeitos)}`", inline=True)

        # Principal suspeito.
        if principal:
            log.add_field(
                "🎯 PRINCIPAL SUSPEITO",
                f"{principal['user'].mention} adicionou o bot "
                f"**{principal['bot_name']}** "
                f"(<t:{int(principal['quando'].timestamp())}:R>)",
                inline=False)

        # Executor (se houver).
        if executor:
            log.set_executor(executor, "Executou raid")

        embed = log.build()

        # ─── Lista de suspeitos. ───
        if suspeitos:
            linhas = []
            for s in suspeitos[:10]:
                user = s["user"]
                emoji = ("🔴" if s["status"] == "cem_por_cento"
                         else "🟡")
                ts = s["timestamp"]
                horario = datetime.fromtimestamp(
                    ts, tz=timezone.utc).strftime("%H:%M:%S")

                # Menciona?
                mention = user.mention if cfg.get(
                    "mencionar_suspeitos") else f"`{user.id}`"

                linha = f"{emoji} {mention} — `{horario}`"
                # Mostra a mensagem (se config).
                if cfg.get("mostrar_mensagem"):
                    content_safe = (s["content"][:60]
                                     .replace("`", "ˋ")
                                     .replace("\n", " "))
                    linha += f"\n   └ `{content_safe}`"

                linhas.append(linha)

            # Divide em campos se for grande.
            texto = "\n".join(linhas)
            if len(texto) > 1000:
                metade = len(linhas) // 2
                embed.add_field(
                    name="🔍 Suspeitos (1/2)",
                    value="\n".join(linhas[:metade])[:1000],
                    inline=False)
                embed.add_field(
                    name="🔍 Suspeitos (2/2)",
                    value="\n".join(linhas[metade:])[:1000],
                    inline=False)
            else:
                embed.add_field(
                    name=f"🔍 Suspeitos ({len(suspeitos)})",
                    value=texto or "*nenhum*",
                    inline=False)

            # Legenda.
            embed.add_field(
                name="Legenda",
                value=(
                    "🔴 **100% culpado** — usou palavra-chave "
                    "(nuke, raid, etc)\n"
                    "🟡 **Suspeito** — usou prefixo+char antes da raid"
                ),
                inline=False)
        else:
            embed.add_field(
                name="🔍 Suspeitos",
                value="*Nenhum suspeito identificado na janela.*",
                inline=False)

        # Comandos úteis.
        embed.add_field(
            name="📌 Comandos",
            value=(
                "`>raidconfig` — painel\n"
                "`>raidconfig log #canal`\n"
                "`>raidconfig janela <min>`\n"
                "`>raidconfig keyword add/remove <palavra>`\n"
                "`>raidlist` — ver suspeitas recentes\n"
                "`>raidclear` — limpar cache de suspeitas"
            ),
            inline=False)

        try:
            await canal.send(embed=embed)
        except Exception:
            pass

        # ─── Notifica owner. ───
        if cfg.get("notificar_owner"):
            try:
                owner = guild.owner or await guild.fetch_member(
                    guild.owner_id)
                if owner:
                    await owner.send(embed=discord.Embed(
                        title="🚨 Raid detectada",
                        description=(
                            f"**Servidor:** {guild.name}\n"
                            f"**Motivo:** {motivo}\n"
                            f"**Suspeitos:** {len(suspeitos)}\n"
                            f"**Principal:** "
                            f"{principal['user'].mention if principal else '—'}"
                        ),
                        color=discord.Color.dark_red(),
                        timestamp=_now()))
            except Exception:
                pass

        # ─── Loga no banco. ───
        try:
            # Registra a detecção completa para consulta posterior em raidstats.
            db = await get_connection_async()
            await db["raid_detections"].insert_one({
                "guild_id": guild.id,
                "motivo": motivo,
                "tipo": tipo,
                "suspeitos": [
                    {"user_id": s["user"].id,
                     "content": s["content"],
                     "status": s["status"],
                     "timestamp": s["timestamp"]}
                    for s in suspeitos[:20]
                ],
                "principal": ({
                    "user_id": principal["user"].id,
                    "bot_id": principal["bot_id"],
                    "bot_name": principal["bot_name"],
                } if principal else None),
                "timestamp": _now(),
            })
        except Exception:
            pass

    # ============================================================
    # COMANDOS
    # ============================================================

    @commands.group(name="raidconfig", aliases=["rconfig"],
                    invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def raidconfig(self, ctx):
        """Painel de configuração do sistema."""
        cfg = await RaidSuspectsManager.get_config(ctx.guild.id)

        def onoff(v):
            return "✅" if v else "❌"

        palavras = cfg.get("palavras_chave_cem_por_cento", [])
        canal_id = cfg.get("canal_raid_log")

        embed = discord.Embed(
            title="🚨 Detecção de Raid — Config",
            description=(
                f"**Status:** {onoff(cfg['enabled'])}\n"
                f"**Auto-detecção:** {onoff(cfg['auto_detectar_raid'])}\n"
                f"**Mencionar suspeitos:** {onoff(cfg['mencionar_suspeitos'])}\n"
                f"**Mostrar mensagens:** {onoff(cfg['mostrar_mensagem'])}\n"
                f"**Notificar owner:** {onoff(cfg['notificar_owner'])}\n"
                f"**Canal de log:** "
                f"{'<#' + str(canal_id) + '>' if canal_id else '❌'}"
            ),
            color=discord.Color.dark_red(),
            timestamp=_now())

        embed.add_field(
            name="⚙️ Parâmetros",
            value=(
                f"**Janela suspeita:** `{cfg['janela_suspeita_min']}min`\n"
                f"**Retenção:** `{cfg['retencao_min']}min`\n"
                f"**Mass join threshold:** `{cfg['raid_join_threshold']}`\n"
                f"**Mass join janela:** `{cfg['raid_join_window']}s`"
            ),
            inline=True)

        embed.add_field(
            name=f"🔑 Palavras-chave ({len(palavras)})",
            value=", ".join(f"`{p}`" for p in palavras[:15])
                  or "*nenhuma*",
            inline=True)

        embed.add_field(
            name="📌 Comandos",
            value=(
                "`>raidconfig on/off`\n"
                "`>raidconfig log #canal`\n"
                "`>raidconfig janela <min>`\n"
                "`>raidconfig keyword add <palavra>`\n"
                "`>raidconfig keyword remove <palavra>`\n"
                "`>raidconfig mention on/off`\n"
                "`>raidconfig showmsg on/off`\n"
                "`>raidconfig threshold <n>`\n"
                "`>raidconfig window <s>`\n"
                "`>raidlist` — ver suspeitas recentes\n"
                "`>raidclear` — limpar cache"
            ),
            inline=False)

        await ctx.send(embed=embed)

    @raidconfig.command(name="on")
    @commands.has_permissions(administrator=True)
    async def rconfig_on(self, ctx):
        """Liga o sistema."""
        await RaidSuspectsManager.update(ctx.guild.id, "enabled", True)
        await ctx.send(embed=embed_success("✅ Sistema ativado."))

    @raidconfig.command(name="off")
    @commands.has_permissions(administrator=True)
    async def rconfig_off(self, ctx):
        """Desliga o sistema."""
        await RaidSuspectsManager.update(ctx.guild.id, "enabled", False)
        await ctx.send(embed=embed_success("✅ Sistema desativado."))

    @raidconfig.command(name="log")
    @commands.has_permissions(administrator=True)
    async def rconfig_log(self, ctx, canal: discord.TextChannel):
        """Define o canal de log."""
        await RaidSuspectsManager.update(ctx.guild.id, "canal_raid_log",
                                     canal.id)
        await ctx.send(embed=embed_success(f"✅ Log em {canal.mention}"))

    @raidconfig.command(name="janela")
    @commands.has_permissions(administrator=True)
    async def rconfig_janela(self, ctx, minutos: int):
        """Define a janela de suspeita (1-120 min)."""
        if minutos < 1 or minutos > 120:
            return await ctx.send(embed=embed_error(
                "❌ Entre 1 e 120 minutos."))
        await RaidSuspectsManager.update(ctx.guild.id, "janela_suspeita_min",
                                     minutos)
        await ctx.send(embed=embed_success(
            f"✅ Janela: `{minutos}min`"))

    @raidconfig.command(name="retencao")
    @commands.has_permissions(administrator=True)
    async def rconfig_retencao(self, ctx, minutos: int):
        """Define a retenção (5-1440 min)."""
        if minutos < 5 or minutos > 1440:
            return await ctx.send(embed=embed_error(
                "❌ Entre 5 e 1440 minutos."))
        await RaidSuspectsManager.update(ctx.guild.id, "retencao_min",
                                     minutos)
        await ctx.send(embed=embed_success(
            f"✅ Retenção: `{minutos}min`"))

    @raidconfig.command(name="threshold")
    @commands.has_permissions(administrator=True)
    async def rconfig_threshold(self, ctx, n: int):
        """Define o mass join threshold (3-50)."""
        if n < 3 or n > 50:
            return await ctx.send(embed=embed_error(
                "❌ Entre 3 e 50."))
        await RaidSuspectsManager.update(ctx.guild.id, "raid_join_threshold", n)
        await ctx.send(embed=embed_success(
            f"✅ Mass join threshold: `{n}`"))

    @raidconfig.command(name="window")
    @commands.has_permissions(administrator=True)
    async def rconfig_window(self, ctx, segundos: int):
        """Define a janela de mass join (3-300s)."""
        if segundos < 3 or segundos > 300:
            return await ctx.send(embed=embed_error(
                "❌ Entre 3 e 300 segundos."))
        await RaidSuspectsManager.update(ctx.guild.id, "raid_join_window",
                                     segundos)
        await ctx.send(embed=embed_success(
            f"✅ Janela: `{segundos}s`"))

    @raidconfig.command(name="mention")
    @commands.has_permissions(administrator=True)
    async def rconfig_mention(self, ctx, state: str):
        """Liga/desliga menção aos suspeitos."""
        enabled = state.lower() in ("on", "true", "1", "sim")
        await RaidSuspectsManager.update(ctx.guild.id, "mencionar_suspeitos",
                                     enabled)
        await ctx.send(embed=embed_success(
            f"✅ Mencionar suspeitos: {'ON' if enabled else 'OFF'}"))

    @raidconfig.command(name="showmsg")
    @commands.has_permissions(administrator=True)
    async def rconfig_showmsg(self, ctx, state: str):
        """Liga/desliga mostrar as mensagens dos suspeitos."""
        enabled = state.lower() in ("on", "true", "1", "sim")
        await RaidSuspectsManager.update(ctx.guild.id, "mostrar_mensagem",
                                     enabled)
        await ctx.send(embed=embed_success(
            f"✅ Mostrar mensagens: {'ON' if enabled else 'OFF'}"))

    @raidconfig.command(name="notify")
    @commands.has_permissions(administrator=True)
    async def rconfig_notify(self, ctx, state: str):
        """Liga/desliga notificar o owner."""
        enabled = state.lower() in ("on", "true", "1", "sim")
        await RaidSuspectsManager.update(ctx.guild.id, "notificar_owner",
                                     enabled)
        await ctx.send(embed=embed_success(
            f"✅ Notificar owner: {'ON' if enabled else 'OFF'}"))

    @raidconfig.command(name="autodetect")
    @commands.has_permissions(administrator=True)
    async def rconfig_autodetect(self, ctx, state: str):
        """Liga/desliga auto-detecção de raid."""
        enabled = state.lower() in ("on", "true", "1", "sim")
        await RaidSuspectsManager.update(ctx.guild.id, "auto_detectar_raid",
                                     enabled)
        await ctx.send(embed=embed_success(
            f"✅ Auto-detecção: {'ON' if enabled else 'OFF'}"))

    # ─── Keywords ───

    @raidconfig.group(name="keyword", aliases=["palavra"],
                       invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def rconfig_keyword(self, ctx):
        """Grupo pra gerenciar palavras-chave."""
        await ctx.send(embed=embed_info(
            "Use: `>raidconfig keyword add/remove <palavra>`"))

    @rconfig_keyword.command(name="add", aliases=["adicionar"])
    @commands.has_permissions(administrator=True)
    async def rconfig_keyword_add(self, ctx, *, palavra: str):
        """Adiciona uma palavra-chave."""
        cfg = await RaidSuspectsManager.get_config(ctx.guild.id)
        palavras = list(cfg.get("palavras_chave_cem_por_cento", []))
        palavra = palavra.lower().strip()
        if palavra in palavras:
            return await ctx.send(embed=embed_warning(
                f"⚠️ `{palavra}` já está na lista."))
        palavras.append(palavra)
        await RaidSuspectsManager.update(
            ctx.guild.id, "palavras_chave_cem_por_cento", palavras)
        await ctx.send(embed=embed_success(
            f"✅ Palavra-chave `{palavra}` adicionada."))

    @rconfig_keyword.command(name="remove", aliases=["remover"])
    @commands.has_permissions(administrator=True)
    async def rconfig_keyword_remove(self, ctx, *, palavra: str):
        """Remove uma palavra-chave."""
        cfg = await RaidSuspectsManager.get_config(ctx.guild.id)
        palavras = list(cfg.get("palavras_chave_cem_por_cento", []))
        palavra = palavra.lower().strip()
        if palavra not in palavras:
            return await ctx.send(embed=embed_warning(
                f"⚠️ `{palavra}` não está na lista."))
        palavras.remove(palavra)
        await RaidSuspectsManager.update(
            ctx.guild.id, "palavras_chave_cem_por_cento", palavras)
        await ctx.send(embed=embed_success(
            f"✅ Palavra-chave `{palavra}` removida."))

    @rconfig_keyword.command(name="list", aliases=["lista"])
    @commands.has_permissions(administrator=True)
    async def rconfig_keyword_list(self, ctx):
        """Lista todas as palavras-chave."""
        cfg = await RaidSuspectsManager.get_config(ctx.guild.id)
        palavras = cfg.get("palavras_chave_cem_por_cento", [])
        if not palavras:
            return await ctx.send(embed=embed_info(
                "📋 Nenhuma palavra-chave cadastrada."))
        embed = discord.Embed(
            title="🔑 Palavras-chave 100% culpado",
            description="\n".join(f"• `{p}`" for p in palavras),
            color=discord.Color.dark_red(),
            timestamp=_now())
        await ctx.send(embed=embed)

    # ─── Comandos úteis ───

    @commands.command(name="raidlist", aliases=["rlist"])
    @commands.has_permissions(kick_members=True)
    async def raidlist(self, ctx, limite: int = 15):
        """Lista as suspeitas recentes registradas."""
        cfg = await RaidSuspectsManager.get_config(ctx.guild.id)
        suspeitos = await self._listar_suspeitos(ctx.guild, cfg)

        if not suspeitos:
            return await ctx.send(embed=embed_info(
                "📋 Nenhuma suspeita registrada na janela recente."))

        embed = discord.Embed(
            title=f"🔍 Suspeitas Recentes ({len(suspeitos)})",
            description=(
                f"Janela: últimos `{cfg['janela_suspeita_min']}min`"
            ),
            color=discord.Color.orange(),
            timestamp=_now())

        for s in suspeitos[:limite]:
            emoji = "🔴" if s["status"] == "cem_por_cento" else "🟡"
            ts = datetime.fromtimestamp(
                s["timestamp"], tz=timezone.utc).strftime("%d/%m %H:%M:%S")
            embed.add_field(
                name=f"{emoji} {s['user']}",
                value=(
                    f"**Quando:** `{ts}`\n"
                    f"**Mensagem:** `{s['content'][:100]}`\n"
                    f"**Motivo:** `{s['motivo']}`"
                ),
                inline=False)

        await ctx.send(embed=embed)

    @commands.command(name="raidclear", aliases=["rclear"])
    @commands.has_permissions(administrator=True)
    async def raidclear(self, ctx):
        """Limpa o cache de suspeitas."""
        self._suspeitas[ctx.guild.id].clear()
        self._joins[ctx.guild.id].clear()
        await ctx.send(embed=embed_success(
            "✅ Cache de suspeitas limpo."))

    @commands.command(name="raidstats", aliases=["rstats"])
    @commands.has_permissions(administrator=True)
    async def raidstats(self, ctx):
        """Estatísticas de detecção de raid."""
        # Conta os registros diretamente no banco sem bloquear o event loop.
        db = await get_connection_async()

        total = await db["raid_detections"].count_documents(
            {"guild_id": ctx.guild.id})
        total_suspeitas = await db["raid_suspects_log"].count_documents(
            {"guild_id": ctx.guild.id})

        # Últimas 24h.
        from datetime import timedelta
        cutoff = _now() - timedelta(hours=24)
        recentes = await db["raid_detections"].count_documents({
            "guild_id": ctx.guild.id,
            "timestamp": {"$gte": cutoff},
        })

        embed = discord.Embed(
            title="📊 Estatísticas de Raid",
            color=discord.Color.dark_red(),
            timestamp=_now())
        embed.add_field(name="🚨 Raids detectadas",
                        value=f"`{total}`", inline=True)
        embed.add_field(name="Últimas 24h",
                        value=f"`{recentes}`", inline=True)
        embed.add_field(name="🔍 Suspeitas registradas",
                        value=f"`{total_suspeitas}`", inline=True)
        embed.add_field(
            name="📌 Cache atual",
            value=f"`{len(self._suspeitas.get(ctx.guild.id, []))}` "
                  f"suspeitas em memória",
            inline=False)
        await ctx.send(embed=embed)


async def setup(bot):
    """Chamado pelo main.py."""
    if bot.get_cog("RaidSuspectsCommands") is None:
        await bot.add_cog(RaidSuspectsCommands(bot))