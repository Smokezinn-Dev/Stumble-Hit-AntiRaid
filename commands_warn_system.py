# ============================================================
# COMMANDS_WARN_SYSTEM.PY - v1.3
# SISTEMA DE AVISOS COM PUNIÇÕES PROGRESSIVAS
# ============================================================
# 📌 O QUE ESSE ARQUIVO FAZ?
# Sistema automático de avisos com punições que aumentam:
#   3 warns → timeout 5min
#   5 warns → timeout 10min
#   10 warns → kick
#   15 warns → ban
#
# Detecta automaticamente 14 tipos de spam (emojis, menções,
# CAPS, flood, zalgo, etc) e aplica warn. Também permite
# aplicar warn manualmente (>warn2 @user motivo).
#
# v1.3 OTIMIZA:
# - Aplicação de warns em massa em PARALELO (útil durante raids)
# - Novo helper público `aplicar_warn_lote()` pra uso interno
# - Cleanup de warnes expirados em PARALELO via task
#
# v1.2 (mantido):
# - Removidas f-strings aninhadas (SyntaxError < 3.12)
# ============================================================

import asyncio
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Union

import discord
from discord.ext import commands, tasks

from database import get_connection_async
from embeds import embed_success, embed_error, embed_warning, embed_info
from embeds_professional import ProfessionalLog, fmt_reason
from utils import TTLCache, GuildGate, run_parallel
from protection_safety import automatic_action_allowed


def _now() -> datetime:
    """Hora atual em UTC."""
    return datetime.now(timezone.utc)


# ============================================================
# REGEX DE DETECÇÃO
# ============================================================
# Padrões pra detectar spam nas mensagens.

EMOJI_CUSTOM_RX = re.compile(r"<a?:\w+:\d+>")
EMOJI_UNICODE_RX = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    "\U00002700-\U000027BF"
    "\U0001F900-\U0001F9FF"
    "\U00002600-\U000026FF"
    "]+",
    flags=re.UNICODE)
INVITE_RX = re.compile(
    r"(discord\.(gg|io|me|li)|discordapp\.com/invite)/[a-zA-Z0-9]+",
    re.IGNORECASE)
URL_RX = re.compile(r"https?://\S+", re.IGNORECASE)
ZALGO_RX = re.compile(r"[\u0300-\u036f\u0489\u1AB0-\u1AFF\u1DC0-\u1DFF]")
INVISIBLE_RX = re.compile(r"[\u200B\u200E\u200F\u202A-\u202E\u2060-\u206F\uFEFF]")


# ============================================================
# REGRAS E NOMES
# ============================================================
# Lista de regras disponíveis + emoji de cada uma.

REGRAS_DISPONIVEIS = {
    "spam_emoji":        "Spam de emojis",
    "spam_emoji_custom": "Spam de emojis customizados",
    "spam_mencoes":      "Spam de menções",
    "spam_everyone":     "Menção a @everyone",
    "spam_here":         "Menção a @here",
    "spam_cargos":       "Spam de menções a cargos",
    "spam_caracteres":   "Spam de caracteres especiais",
    "spam_caps":         "Uso excessivo de CAPS LOCK",
    "spam_repetidos":    "Spam de caracteres repetidos",
    "spam_flood":        "Flood de mensagens",
    "spam_convite":      "Convite do Discord",
    "spam_links":        "Spam de links",
    "spam_zalgo":        "Zalgo / caracteres estranhos",
    "spam_invisiveis":   "Caracteres invisíveis",
    "manual_staff":      "Aplicado por staff",
    "manual_sistema":    "Aplicado pelo sistema",
}

REGRAS_EMOJI = {
    "spam_emoji": "😀",
    "spam_emoji_custom": "🎨",
    "spam_mencoes": "📢",
    "spam_everyone": "🚨",
    "spam_here": "📍",
    "spam_cargos": "🏷️",
    "spam_caracteres": "✳️",
    "spam_caps": "🔠",
    "spam_repetidos": "🔁",
    "spam_flood": "📨",
    "spam_convite": "🔗",
    "spam_links": "🌐",
    "spam_zalgo": "👹",
    "spam_invisiveis": "👻",
    "manual_staff": "🛡️",
    "manual_sistema": "⚙️",
}


# ============================================================
# CONFIGURAÇÃO PADRÃO
# ============================================================

DEFAULT_CONFIG = {
    "enabled": True,
    "auto_apply": False,   # se True, aplica punição direto; senão, pede confirmação

    # Detecções (on/off).
    "detect_spam_emoji": True,
    "detect_spam_emoji_custom": True,
    "detect_spam_mencoes": True,
    "detect_spam_everyone": True,
    "detect_spam_here": True,
    "detect_spam_cargos": True,
    "detect_spam_caracteres": True,
    "detect_spam_caps": True,
    "detect_spam_repetidos": True,
    "detect_spam_flood": True,
    "detect_spam_convite": True,
    "detect_spam_links": True,
    "detect_spam_zalgo": True,
    "detect_spam_invisiveis": True,

    # Limites (quando dispara).
    "limite_emoji": 10,
    "limite_emoji_custom": 8,
    "limite_mencoes": 5,
    "limite_cargos": 3,
    "limite_caracteres": 50,      # percentual
    "limite_caps": 80,            # percentual
    "limite_repetidos": 15,
    "limite_flood": 5,
    "limite_links": 3,
    "limite_zalgo": 5,

    "janela_tempo": 30,           # janela de detecção (segundos)

    "expira_confirmacao_min": 5,  # confirmação via DM expira
    "notificar_owner": True,

    "log_canal": 0,

    # Quem pode aplicar/confirmar warns.
    "cargos_aplicar": [],
    "users_aplicar": [],
    "cargos_confirmar": [],
    "users_confirmar": [],
    "cargos_confirmar_exceto": {},
    "users_confirmar_exceto": {},

    # Cargos imunes a warn.
    "cargos_imunes": [],
    "cargos_imunes_exceto": {},

    # Punições progressivas (avisos → ação).
    "punicoes": [
        {"avisos": 3,  "acao": "timeout", "duracao_min": 5},
        {"avisos": 5,  "acao": "timeout", "duracao_min": 10},
        {"avisos": 10, "acao": "kick",    "duracao_min": 0},
        {"avisos": 15, "acao": "ban",     "duracao_min": 0},
    ],
}


# ============================================================
# MANAGER
# ============================================================

class WarnManager:
    _cache = TTLCache(max_size=200, ttl=60)

    @classmethod
    async def get_config(cls, guild_id: int) -> dict:
        """Pega config (cache OU banco)."""
        cached = cls._cache.get(guild_id)
        if cached is not None:
            return cached
        # Motor fornece operações assíncronas; aguardamos a leitura para não bloquear o loop do Discord.
        db = await get_connection_async()
        doc = await db["warn_system_config"].find_one({"guild_id": guild_id}) or {}
        cfg = {**DEFAULT_CONFIG, **doc}
        cls._cache.set(guild_id, cfg)
        return cfg

    @classmethod
    async def update(cls, guild_id: int, key: str, value):
        """Salva uma config."""
        # Primeiro persistimos no MongoDB; só depois invalidamos o cache local.
        db = await get_connection_async()
        await db["warn_system_config"].update_one(
            {"guild_id": guild_id}, {"$set": {key: value}}, upsert=True)
        cls._cache.invalidate(guild_id)

    @classmethod
    def invalidate(cls, guild_id: int):
        cls._cache.invalidate(guild_id)


# ============================================================
# PERMISSÕES
# ============================================================

def _pode_aplicar(cfg: dict, member: discord.Member) -> bool:
    """Diz se o membro pode aplicar warns."""
    if member.id == member.guild.owner_id:
        return True
    if member.id in cfg.get("users_aplicar", []):
        return True
    cargos = set(cfg.get("cargos_aplicar", []))
    if any(r.id in cargos for r in member.roles):
        return True
    return False


def _pode_confirmar(cfg: dict, member: discord.Member) -> bool:
    """Diz se o membro pode CONFIRMAR uma punição."""
    if member.id == member.guild.owner_id:
        return True

    # Verifica user.
    if member.id in cfg.get("users_confirmar", []):
        exceto = cfg.get("users_confirmar_exceto", {}).get(str(member.id), [])
        if member.id not in exceto:
            return True

    # Verifica cargo.
    cargos = set(cfg.get("cargos_confirmar", []))
    exceto_por_cargo = cfg.get("cargos_confirmar_exceto", {})
    for r in member.roles:
        if r.id in cargos:
            excepted = exceto_por_cargo.get(str(r.id), [])
            if member.id not in excepted:
                return True
    return False


def _cargo_imune(cfg: dict, member: discord.Member,
                  autor_id: int) -> bool:
    """Diz se o membro é IMUNE a warns."""
    imunes = set(cfg.get("cargos_imunes", []))
    exceto = cfg.get("cargos_imunes_exceto", {})

    for r in member.roles:
        if r.id in imunes:
            # Se o autor está na lista de exceção, pode punir mesmo imune.
            permitidos = exceto.get(str(r.id), [])
            if autor_id not in permitidos:
                return True
    return False


# ============================================================
# CONFIRMAÇÃO VIA DM (view com botões)
# ============================================================

class ConfirmarPunicaoView(discord.ui.View):
    def __init__(self, cog, confirm_id: str, guild: discord.Guild):
        super().__init__(timeout=None)
        self.cog = cog
        self.confirm_id = confirm_id
        self.guild = guild

    @discord.ui.button(label="✅ Confirmar punição",
                        style=discord.ButtonStyle.danger,
                        custom_id="warn_confirm_yes")
    async def confirmar(self, interaction: discord.Interaction,
                         button: discord.ui.Button):
        await self.cog._processar_voto(interaction, self.confirm_id, True)

    @discord.ui.button(label="❌ Cancelar",
                        style=discord.ButtonStyle.secondary,
                        custom_id="warn_confirm_no")
    async def cancelar(self, interaction: discord.Interaction,
                        button: discord.ui.Button):
        await self.cog._processar_voto(interaction, self.confirm_id, False)


# ============================================================
# COG
# ============================================================

class WarnSystemCommands(commands.Cog):
    """Sistema de avisos com punições progressivas."""

    def __init__(self, bot):
        self.bot = bot

        # Cache de detecções: {guild_id: {user_id: deque[(timestamp, regra)]}}
        self._deteccoes: Dict[int, Dict[int, deque]] = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=20)))

        # Confirmações pendentes.
        self._pendentes: Dict[str, dict] = {}
        self._resultados: Dict[str, str] = {}

        # Inicia tasks.
        self._expire_task.start()
        self._cleanup_task.start()

    def cog_unload(self):
        """Cancela tasks ao descarregar."""
        try:
            self._expire_task.cancel()
        except Exception:
            pass
        try:
            self._cleanup_task.cancel()
        except Exception:
            pass

    # ============================================================
    # EXPIRAÇÃO DE CONFIRMAÇÕES
    # ============================================================

    @tasks.loop(seconds=30)
    async def _expire_task(self):
        """Expira confirmações antigas."""
        now = _now()
        for cid in list(self._pendentes.keys()):
            info = self._pendentes[cid]
            if now >= info["expires_at"]:
                await self._finalizar_confirmacao(cid, "expirou")
                self._pendentes.pop(cid, None)

    @_expire_task.before_loop
    async def _before_expire(self):
        await self.bot.wait_until_ready()

    # ============================================================
    # CLEANUP DE WARNS EXPIRADOS
    # ============================================================

    @tasks.loop(hours=6)
    async def _cleanup_task(self):
        """Remove warns inativos com mais de 90 dias."""
        try:
            db = await get_connection_async()
            cutoff = _now() - timedelta(days=90)
            # Bulk delete (rápido); cada operação Motor precisa ser aguardada.
            await db["warn_system_warns"].delete_many({
                "ativo": False,
                "timestamp": {"$lt": cutoff},
            })
            await db["warn_system_punicoes"].delete_many({
                "timestamp": {"$lt": cutoff},
            })
        except Exception:
            pass

    @_cleanup_task.before_loop
    async def _before_cleanup(self):
        await self.bot.wait_until_ready()

    # ============================================================
    # API: APLICAR WARN (individual)
    # ============================================================

    async def aplicar_warn(
        self,
        guild: discord.Guild,
        user: discord.Member,
        regra: str,
        motivo: str = "",
        aplicado_por: Optional[discord.Member] = None,
        evidencia: str = "",
        canal: Optional[discord.abc.Messageable] = None,
    ) -> Optional[str]:
        """
        Aplica um warn a um user.
        Retorna o ID do warn (ou None se falhou).

        canal: canal onde o ato foi cometido. O aviso é enviado LÁ
        (não mais em DM), para o usuário ver imediatamente.
        """
        cfg = await WarnManager.get_config(guild.id)
        if not cfg.get("enabled"):
            return None
        # Warns automáticos respeitam o toggle humano e a whitelist central.
        # Warn manual de staff continua registrável, mas a escalada automática
        # é revalidada separadamente antes da punição.
        if aplicado_por is None and not await automatic_action_allowed(guild.id, user):
            return None

        autor_id = aplicado_por.id if aplicado_por else self.bot.user.id

        # Checa se o user é imune.
        if _cargo_imune(cfg, user, autor_id):
            return None

        # Salva o warn no banco.
        db = await get_connection_async()
        warn_data = {
            "guild_id": guild.id,
            "user_id": user.id,
            "regra": regra,
            "motivo": motivo,
            "aplicado_por": autor_id,
            "aplicado_por_nome": str(aplicado_por) if aplicado_por else "Sistema",
            "evidencia": evidencia,
            "timestamp": _now(),
            "ativo": True,
        }
        result = await db["warn_system_warns"].insert_one(warn_data)
        warn_id = str(result.inserted_id)

        # Conta total de warns ativos do user sem bloquear o event loop.
        total = await db["warn_system_warns"].count_documents(
            {"guild_id": guild.id, "user_id": user.id, "ativo": True})

        # Loga o warn + avisa no canal do ato (não em DM).
        aviso_enviado = await self._log_warn_aplicado(
            guild, user, warn_id, regra, motivo, aplicado_por, total,
            canal=canal)

        # Não aplica punição escalonada se o aviso não pôde ser entregue.
        # O warn fica registrado para auditoria, mas não pune silenciosamente.
        if not aviso_enviado:
            return warn_id

        # Verifica se bateu alguma punição.
        punicao = self._proxima_punicao(cfg, total)
        if punicao:
            # Aplica direto ou pede confirmação?
            if cfg.get("auto_apply"):
                await self._aplicar_punicao(guild, user, punicao,
                                             f"{total} avisos")
            else:
                await self._pedir_confirmacao(
                    guild, user, warn_id, regra, motivo,
                    aplicado_por, punicao, total)

        return warn_id

    # ============================================================
    # API NOVA: APLICAR LOTE (v1.3 — PARALELO)
    # ============================================================

    async def aplicar_warn_lote(
        self,
        guild: discord.Guild,
        jobs: List[dict],
        aplicado_por: Optional[discord.Member] = None,
        canal: Optional[discord.abc.Messageable] = None,
    ) -> Dict[str, int]:
        """
        Aplica múltiplos warns em paralelo.

        jobs: lista de dicts com:
          {
            "user": discord.Member,
            "regra": str,
            "motivo": str (opcional),
            "evidencia": str (opcional),
          }

        Retorna: {"ok": N, "fail": N, "skipped": N}
        """
        cfg = await WarnManager.get_config(guild.id)
        if not cfg.get("enabled"):
            return {"ok": 0, "fail": 0, "skipped": len(jobs)}

        autor_id = aplicado_por.id if aplicado_por else self.bot.user.id
        stats = {"ok": 0, "fail": 0, "skipped": 0}

        async def _worker(job):
            """Aplica 1 warn."""
            try:
                user: discord.Member = job["user"]
                # Se é imune, pula.
                if _cargo_imune(cfg, user, autor_id):
                    stats["skipped"] += 1
                    return

                await self.aplicar_warn(
                    guild=guild,
                    user=user,
                    regra=job.get("regra", "manual_sistema"),
                    motivo=job.get("motivo", ""),
                    aplicado_por=aplicado_por,
                    evidencia=job.get("evidencia", ""),
                    canal=canal,
                )
                stats["ok"] += 1
            except Exception:
                stats["fail"] += 1

        # Roda em paralelo (4 ao mesmo tempo).
        await run_parallel(jobs, _worker, concurrency=4, retry=1)
        return stats

    def _proxima_punicao(self, cfg: dict,
                          total_warns: int) -> Optional[dict]:
        """Verifica se o user bateu alguma punição (por total de warns)."""
        punicoes = cfg.get("punicoes", [])
        # Ordena por avisos.
        punicoes = sorted(punicoes, key=lambda p: p.get("avisos", 0))
        # Retorna a punição EXATA se bateu.
        for p in punicoes:
            if p.get("avisos") == total_warns:
                return p
        return None

    # ============================================================
    # CONFIRMAÇÃO VIA DM
    # ============================================================

    async def _pedir_confirmacao(
        self,
        guild: discord.Guild,
        user: discord.Member,
        warn_id: str,
        regra: str,
        motivo: str,
        autor: Optional[discord.Member],
        punicao: dict,
        total_warns: int,
    ):
        """Pede confirmação via DM pra quem pode confirmar."""
        cfg = await WarnManager.get_config(guild.id)

        # Lista quem pode confirmar.
        confirmadores = await self._listar_confirmadores(guild, cfg)
        if not confirmadores:
            # Ninguém pode confirmar → aplica direto.
            await self._aplicar_punicao(guild, user, punicao,
                                         f"{total_warns} avisos")
            return

        # Gera ID único.
        cid = secrets.token_urlsafe(8)
        expires_at = _now() + timedelta(
            minutes=int(cfg.get("expira_confirmacao_min", 5)))

        # Guarda info.
        info = {
            "guild_id": guild.id,
            "user_id": user.id,
            "warn_id": warn_id,
            "regra": regra,
            "motivo": motivo,
            "autor_id": autor.id if autor else 0,
            "autor_nome": str(autor) if autor else "Sistema",
            "punicao": punicao,
            "total_warns": total_warns,
            "votes": {},
            "message_ids": {},
            "expires_at": expires_at,
        }
        self._pendentes[cid] = info

        view = ConfirmarPunicaoView(self, cid, guild)

        regra_label = REGRAS_DISPONIVEIS.get(regra, regra)
        regra_emoji = REGRAS_EMOJI.get(regra, "⚠️")

        # Monta embed.
        embed = discord.Embed(
            title="⚠️ Confirmação de Punição",
            description=(
                f"Um usuário atingiu **{total_warns} avisos** e uma "
                f"punição deve ser aplicada.\n\n"
                f"Precisamos da sua confirmação."
            ),
            color=discord.Color.orange(),
            timestamp=_now())
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(
            name="👤 Usuário",
            value=f"{user.mention}\n`{user.id}`",
            inline=True)
        embed.add_field(
            name=f"{regra_emoji} Regra Quebrada",
            value=f"`{regra_label}`",
            inline=True)
        embed.add_field(
            name="📊 Total de Avisos",
            value=f"`{total_warns}`",
            inline=True)
        embed.add_field(
            name="⚖️ Punição Sugerida",
            value=self._descrever_punicao(punicao),
            inline=False)
        if motivo:
            embed.add_field(name="📝 Motivo", value=motivo[:500],
                            inline=False)
        embed.add_field(
            name="⏰ Expira em",
            value=f"<t:{int(expires_at.timestamp())}:R>",
            inline=False)
        embed.set_footer(text=f"ID: {cid}")

        # Envia pra cada confirmador.
        enviados = 0
        for conf in confirmadores:
            try:
                msg = await conf.send(embed=embed, view=view)
                info["message_ids"][conf.id] = msg.id
                enviados += 1
            except Exception:
                pass

        # Se ninguém recebeu, aplica direto.
        if enviados == 0:
            self._pendentes.pop(cid, None)
            await self._aplicar_punicao(guild, user, punicao,
                                         f"{total_warns} avisos")

    async def _listar_confirmadores(self, guild: discord.Guild,
                                      cfg: dict) -> List[discord.Member]:
        """Lista quem pode confirmar."""
        confirmadores = []
        seen = set()

        # Owner.
        try:
            owner = guild.owner or await guild.fetch_member(guild.owner_id)
            if owner:
                confirmadores.append(owner)
                seen.add(owner.id)
        except Exception:
            pass

        # Users específicos.
        for uid in cfg.get("users_confirmar", []):
            if uid in seen:
                continue
            m = guild.get_member(uid)
            if m and _pode_confirmar(cfg, m):
                confirmadores.append(m)
                seen.add(uid)

        # Membros de cargos.
        for rid in cfg.get("cargos_confirmar", []):
            role = guild.get_role(rid)
            if not role:
                continue
            for m in role.members:
                if m.id in seen:
                    continue
                if m.bot:
                    continue
                if _pode_confirmar(cfg, m):
                    confirmadores.append(m)
                    seen.add(m.id)

        return confirmadores

    async def _processar_voto(self, interaction: discord.Interaction,
                                confirm_id: str, aprovado: bool):
        """Processa um voto (aprovar/rejeitar)."""
        # Já foi resolvido?
        if confirm_id not in self._pendentes:
            return await interaction.response.send_message(
                "⚠️ Esta confirmação já foi resolvida ou expirou.",
                ephemeral=True)

        info = self._pendentes[confirm_id]
        guild = self.bot.get_guild(info["guild_id"])
        if not guild:
            return await interaction.response.send_message(
                "❌ Servidor não encontrado.", ephemeral=True)

        cfg = await WarnManager.get_config(guild.id)
        autor = guild.get_member(interaction.user.id)

        # Verifica permissão.
        if not autor or not _pode_confirmar(cfg, autor):
            await self._tentar_sem_permissao(guild, interaction.user, info)
            return await interaction.response.send_message(
                "❌ Você **não tem permissão** para confirmar esta punição.",
                ephemeral=True)

        # Registra o voto.
        info["votes"][interaction.user.id] = aprovado

        # ─── REJEITADO. ───
        if not aprovado:
            self._resultados[confirm_id] = "rejeitada"
            await interaction.response.send_message(
                "❌ Você **cancelou** a punição.", ephemeral=True)
            await self._finalizar_confirmacao(confirm_id, "rejeitada")
            self._pendentes.pop(confirm_id, None)
            return

        # ─── APROVADO. ───
        self._resultados[confirm_id] = "aprovada"
        await interaction.response.send_message(
            "✅ Você **aprovou** a punição. Executando...", ephemeral=True)
        await self._finalizar_confirmacao(confirm_id, "aprovada")
        self._pendentes.pop(confirm_id, None)

        # Aplica a punição.
        user = guild.get_member(info["user_id"])
        if user:
            await self._aplicar_punicao(
                guild, user, info["punicao"],
                f"{info['total_warns']} avisos")

    async def _tentar_sem_permissao(self, guild: discord.Guild,
                                      user: discord.User,
                                      info: dict):
        """Loga tentativa de confirmar sem permissão."""
        cfg = await WarnManager.get_config(guild.id)

        log = ProfessionalLog(
            title="Tentativa Não Autorizada",
            description=(
                "Um usuário tentou confirmar uma punição sem ter "
                "permissão para isso."
            ),
            severity="medium",
            footer="Stumble Hit AntiRaid • Sistema de Avisos")
        log.set_target(user if isinstance(user, discord.Member) else None)
        log.add_field("Ação", "Tentar confirmar punição")
        log.add_field(
            "ID da Confirmação",
            f"`{list(self._pendentes.keys())[0][:8]}...`")
        await self._logar_no_canal(guild, cfg, log.build())

        # Notifica owner.
        if cfg.get("notificar_owner"):
            try:
                owner = guild.owner or await guild.fetch_member(
                    guild.owner_id)
                if owner:
                    await owner.send(embed=discord.Embed(
                        title="🚨 Tentativa não autorizada",
                        description=(
                            f"**{user}** (`{user.id}`) tentou confirmar "
                            f"uma punição em **{guild.name}** sem "
                            f"permissão."
                        ),
                        color=discord.Color.orange(),
                        timestamp=_now()))
            except Exception:
                pass

    async def _finalizar_confirmacao(self, confirm_id: str,
                                       resultado: str):
        """Edita as DMs originais com o resultado."""
        info = self._pendentes.get(confirm_id)
        if not info:
            return

        self._resultados[confirm_id] = resultado

        guild = self.bot.get_guild(info["guild_id"])
        if not guild:
            return

        for admin_id, msg_id in info.get("message_ids", {}).items():
            try:
                admin = guild.get_member(admin_id)
                if not admin:
                    continue
                msg = await admin.fetch_message(msg_id)
                color = (discord.Color.green() if resultado == "aprovada"
                         else discord.Color.red() if resultado == "rejeitada"
                         else discord.Color.greyple())
                await msg.edit(
                    embed=discord.Embed(
                        title=f"Resultado: {resultado.title()}",
                        description=(
                            f"Punição para <@{info['user_id']}> "
                            f"foi **{resultado}**."
                        ),
                        color=color,
                        timestamp=_now()),
                    view=None)
            except Exception:
                pass

    # ============================================================
    # APLICAR PUNIÇÃO
    # ============================================================

    async def _aplicar_punicao(self, guild: discord.Guild,
                                 user: discord.Member,
                                 punicao: dict, motivo_base: str):
        """Aplica a punição configurada."""
        acao = punicao.get("acao", "timeout")
        dur_min = int(punicao.get("duracao_min", 0))
        motivo = f"Warn System: {motivo_base}"

        # A escalada é sempre automática, inclusive quando os warns anteriores
        # foram manuais: respeita toggle humano e whitelist no momento da ação.
        if not await automatic_action_allowed(guild.id, user):
            return

        try:
            # Última validação imediatamente antes da ação externa.
            if not await automatic_action_allowed(guild.id, user):
                return
            # Aplica a ação.
            if acao == "timeout":
                until = discord.utils.utcnow() + timedelta(minutes=dur_min)
                await user.timeout(until, reason=motivo)
            elif acao == "kick":
                await user.kick(reason=motivo)
            elif acao == "ban":
                await user.ban(reason=motivo, delete_message_days=1)
            elif acao == "strip":
                # Remove todos os cargos.
                roles_danger = [r for r in user.roles
                                if not r.is_default()]
                if roles_danger:
                    await user.remove_roles(*roles_danger, reason=motivo)
        except Exception:
            pass

        # Salva no banco.
        db = await get_connection_async()
        await db["warn_system_punicoes"].insert_one({
            "guild_id": guild.id,
            "user_id": user.id,
            "acao": acao,
            "duracao_min": dur_min,
            "motivo": motivo_base,
            "timestamp": _now(),
        })

        # Envia log.
        log = ProfessionalLog(
            title="Punição Aplicada",
            description=(
                f"O usuário atingiu **{motivo_base}** e a punição "
                f"configurada foi aplicada."
            ),
            severity="high",
            footer="Stumble Hit AntiRaid • Sistema de Avisos")
        log.set_target(user)
        log.add_field("Ação", f"`{acao}`")
        if dur_min:
            log.add_field("Duração", f"`{dur_min}min`")
        await self._logar_no_canal(guild, await WarnManager.get_config(guild.id),
                                     log.build())

    # ============================================================
    # DETECÇÕES AUTOMÁTICAS
    # ============================================================

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Analisa TODA mensagem em busca de spam."""
        if not message.guild or message.author.bot:
            return

        cfg = await WarnManager.get_config(message.guild.id)
        if not cfg.get("enabled"):
            return

        member = message.author
        # Admin não é afetado.
        if member.guild_permissions.administrator:
            return
        # Cargo imune?
        if _cargo_imune(cfg, member, self.bot.user.id):
            return

        content = message.content or ""
        # Mensagem vazia ou só whitespace → não analisa (evita falso positivo).
        if not content.strip():
            return
        regras_detectadas = []

        # ─── Detecta cada tipo. ───

        # Emojis.
        if cfg.get("detect_spam_emoji"):
            total = (len(EMOJI_CUSTOM_RX.findall(content))
                     + len(EMOJI_UNICODE_RX.findall(content)))
            if total >= int(cfg.get("limite_emoji", 10)):
                regras_detectadas.append(
                    ("spam_emoji", f"{total} emojis"))

        # Emojis custom.
        if cfg.get("detect_spam_emoji_custom"):
            total = len(EMOJI_CUSTOM_RX.findall(content))
            if total >= int(cfg.get("limite_emoji_custom", 8)):
                regras_detectadas.append(
                    ("spam_emoji_custom", f"{total} custom"))

        # Menções.
        if cfg.get("detect_spam_mencoes"):
            total = len(message.mentions)
            if total >= int(cfg.get("limite_mencoes", 5)):
                regras_detectadas.append(
                    ("spam_mencoes", f"{total} menções"))

        # @everyone.
        if cfg.get("detect_spam_everyone") and message.mention_everyone:
            if "@everyone" in content.lower():
                regras_detectadas.append(
                    ("spam_everyone", "@everyone"))

        # @here.
        if cfg.get("detect_spam_here") and "@here" in content.lower():
            regras_detectadas.append(("spam_here", "@here"))

        # Cargos.
        if cfg.get("detect_spam_cargos"):
            total = len(message.role_mentions)
            if total >= int(cfg.get("limite_cargos", 3)):
                regras_detectadas.append(
                    ("spam_cargos", f"{total} cargos"))

        # Caracteres especiais.
        if cfg.get("detect_spam_caracteres") and len(content) >= 20:
            total_chars = len(content)
            especiais = sum(1 for c in content
                            if not c.isalnum() and not c.isspace())
            pct = (especiais / total_chars) * 100
            if pct >= int(cfg.get("limite_caracteres", 50)):
                regras_detectadas.append(
                    ("spam_caracteres", f"{pct:.0f}% especiais"))

        # CAPS.
        if cfg.get("detect_spam_caps"):
            letras = [c for c in content if c.isalpha()]
            if len(letras) >= 10:
                caps = sum(1 for c in letras if c.isupper())
                pct = (caps / len(letras)) * 100
                if pct >= int(cfg.get("limite_caps", 80)):
                    regras_detectadas.append(
                        ("spam_caps", f"{pct:.0f}% CAPS"))

        # Repetidos (ignora whitespace para evitar falso positivo em espaços).
        if cfg.get("detect_spam_repetidos"):
            max_run = 1
            cur_run = 1
            for i in range(1, len(content)):
                # Espaço/tab/newline não contam como "spam de repetidos".
                if content[i].isspace() or content[i - 1].isspace():
                    cur_run = 1
                    continue
                if content[i] == content[i - 1]:
                    cur_run += 1
                    max_run = max(max_run, cur_run)
                else:
                    cur_run = 1
            if max_run >= int(cfg.get("limite_repetidos", 15)):
                regras_detectadas.append(
                    ("spam_repetidos", f"{max_run} repetidos"))

        # Convite.
        if cfg.get("detect_spam_convite") and INVITE_RX.search(content):
            regras_detectadas.append(("spam_convite", "convite"))

        # Links.
        if cfg.get("detect_spam_links"):
            total = len(URL_RX.findall(content))
            if total >= int(cfg.get("limite_links", 3)):
                regras_detectadas.append(
                    ("spam_links", f"{total} links"))

        # Zalgo.
        if cfg.get("detect_spam_zalgo"):
            total = len(ZALGO_RX.findall(content))
            if total >= int(cfg.get("limite_zalgo", 5)):
                regras_detectadas.append(
                    ("spam_zalgo", f"{total} diacríticos"))

        # Invisíveis reais (ZWS/RTL/BOM). Espaço normal NÃO conta.
        # Mensagem só com whitespace comum também não conta.
        if cfg.get("detect_spam_invisiveis"):
            if content.strip() and INVISIBLE_RX.search(content):
                regras_detectadas.append(
                    ("spam_invisiveis", "invisíveis"))

        # Nada detectado?
        if not regras_detectadas:
            return

        # Pega só a primeira regra quebrada.
        regra, detalhe = regras_detectadas[0]

        # Anti-spam de detecção (não punir 50x o mesmo).
        janela = float(cfg.get("janela_tempo", 30))
        agora = time.time()
        dq = self._deteccoes[message.guild.id][member.id]
        while dq and agora - dq[0][0] > janela:
            dq.popleft()

        # Já foi punido por essa regra recentemente?
        for ts, r in dq:
            if r == regra:
                return
        dq.append((agora, regra))

        # Aplica o warn (aviso vai no canal do ato, não em DM).
        await self.aplicar_warn(
            guild=message.guild,
            user=member,
            regra=regra,
            motivo=f"Detecção automática: {detalhe}",
            canal=message.channel,
        )

        # Deleta a mensagem.
        try:
            await message.delete()
        except Exception:
            pass

    # ============================================================
    # LOGS
    # ============================================================

    async def _log_warn_aplicado(self, guild, user, warn_id, regra,
                                   motivo, autor, total, canal=None):
        """Loga um warn aplicado e avisa o user no canal do ato (não em DM)."""
        cfg = await WarnManager.get_config(guild.id)

        regra_label = REGRAS_DISPONIVEIS.get(regra, regra)
        regra_emoji = REGRAS_EMOJI.get(regra, "⚠️")

        log = ProfessionalLog(
            title="Aviso Aplicado",
            description="Um aviso foi registrado para um usuário.",
            severity="medium",
            footer="Stumble Hit AntiRaid • Sistema de Avisos")
        log.set_target(user)
        if autor:
            log.set_executor(autor)
        log.add_field("Regra Quebrada", f"{regra_emoji} `{regra_label}`")
        log.add_field("Total de Avisos", f"`{total}`")
        log.add_field("ID do Aviso", f"`{warn_id[:8]}`")
        if motivo:
            log.add_field("Detalhes", fmt_reason(motivo), inline=False)
        await self._logar_no_canal(guild, cfg, log.build())

        # Aviso no chat onde o ato foi cometido (não mais em DM).
        # Assim o usuário vê imediatamente, antes de qualquer punição.
        aviso_embed = discord.Embed(
            title="⚠️ Você recebeu um aviso",
            description=(
                f"{user.mention}, você quebrou uma regra.\n\n"
                f"**Regra:** {regra_emoji} {regra_label}\n"
                f"**Detalhes:** {motivo or '—'}\n"
                f"**Total de avisos:** `{total}`"
            ),
            color=discord.Color.orange(),
            timestamp=_now())
        aviso_embed.set_footer(text="Stumble Hit AntiRaid • Sistema de Avisos")

        target_channel = canal
        if target_channel is None:
            # Fallback: tenta canal de log se não veio o canal do ato.
            canal_id = cfg.get("log_canal")
            if canal_id:
                target_channel = guild.get_channel(int(canal_id))

        if target_channel is not None:
            try:
                await target_channel.send(
                    content=user.mention,
                    embed=aviso_embed,
                    delete_after=60)
                return True
            except Exception:
                return False
        return False

    async def _logar_no_canal(self, guild, cfg, embed):
        """Envia um embed pro canal de log."""
        canal_id = cfg.get("log_canal")
        if not canal_id:
            return
        canal = guild.get_channel(int(canal_id))
        if canal:
            try:
                await canal.send(embed=embed)
            except Exception:
                pass

    @staticmethod
    def _descrever_punicao(punicao: dict) -> str:
        """Descreve uma punição como texto."""
        acao = punicao.get("acao", "?")
        dur = punicao.get("duracao_min", 0)
        if acao == "timeout":
            return f"`⏰ Timeout de {dur}min`"
        if acao == "kick":
            return "`👢 Expulsão`"
        if acao == "ban":
            return "`🔨 Banimento`"
        if acao == "strip":
            return "`📌 Remover cargos`"
        return f"`{acao}`"

    # ============================================================
    # COMANDOS
    # ============================================================

    @commands.group(name="wsys", aliases=["warnsys"],
                    invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def wsys(self, ctx):
        """Painel do sistema de avisos."""
        cfg = await WarnManager.get_config(ctx.guild.id)

        def onoff(v):
            return "✅" if v else "❌"

        log_canal = cfg.get("log_canal")
        if log_canal:
            log_str = f"<#{log_canal}>"
        else:
            log_str = "❌"

        auto_apply_str = ("aplica direto"
                          if cfg['auto_apply'] else "pede confirmação")

        embed = discord.Embed(
            title="⚠️ Sistema de Avisos",
            description=(
                f"**Status:** {onoff(cfg['enabled'])}\n"
                f"**Auto-apply:** {onoff(cfg['auto_apply'])} "
                f"({auto_apply_str})\n"
                f"**Janela de tempo:** `{cfg['janela_tempo']}s`\n"
                f"**Log:** {log_str}"
            ),
            color=discord.Color.orange(),
            timestamp=_now())

        embed.add_field(
            name="🔍 Detecções",
            value=(
                f"{onoff(cfg['detect_spam_emoji'])} Emojis\n"
                f"{onoff(cfg['detect_spam_emoji_custom'])} Emojis custom\n"
                f"{onoff(cfg['detect_spam_mencoes'])} Menções\n"
                f"{onoff(cfg['detect_spam_everyone'])} @everyone\n"
                f"{onoff(cfg['detect_spam_here'])} @here\n"
                f"{onoff(cfg['detect_spam_cargos'])} Cargos\n"
                f"{onoff(cfg['detect_spam_caracteres'])} Caracteres\n"
                f"{onoff(cfg['detect_spam_caps'])} CAPS\n"
                f"{onoff(cfg['detect_spam_repetidos'])} Repetidos\n"
                f"{onoff(cfg['detect_spam_flood'])} Flood\n"
                f"{onoff(cfg['detect_spam_convite'])} Convite\n"
                f"{onoff(cfg['detect_spam_links'])} Links\n"
                f"{onoff(cfg['detect_spam_zalgo'])} Zalgo\n"
                f"{onoff(cfg['detect_spam_invisiveis'])} Invisíveis"
            ),
            inline=True)

        embed.add_field(
            name="📊 Limites",
            value=(
                f"Emoji: `{cfg['limite_emoji']}`\n"
                f"Custom: `{cfg['limite_emoji_custom']}`\n"
                f"Menções: `{cfg['limite_mencoes']}`\n"
                f"Cargos: `{cfg['limite_cargos']}`\n"
                f"Chars: `{cfg['limite_caracteres']}%`\n"
                f"CAPS: `{cfg['limite_caps']}%`\n"
                f"Repetidos: `{cfg['limite_repetidos']}`\n"
                f"Flood: `{cfg['limite_flood']}`\n"
                f"Links: `{cfg['limite_links']}`\n"
                f"Zalgo: `{cfg['limite_zalgo']}`"
            ),
            inline=True)

        # Lista as punições.
        punicoes = cfg.get("punicoes", [])
        linhas = []
        for p in punicoes:
            linhas.append(
                f"• `{p['avisos']}` avisos → "
                f"{self._descrever_punicao(p)}")
        embed.add_field(
            name="⚖️ Punições Progressivas",
            value="\n".join(linhas) or "*Nenhuma*",
            inline=False)

        embed.add_field(
            name="📌 Comandos",
            value=(
                "`>wsys on/off` — liga/desliga\n"
                "`>wsys autoapply on/off` — aplica direto\n"
                "`>wsys log #canal` — canal de log\n"
                "`>wsys detect <regra> on/off`\n"
                "`>wsys limite <regra> <n>`\n"
                "`>wsys janela <s>`\n"
                "`>wsys addpunish <avisos> <acao> [duração]`\n"
                "`>wsys removepunish <avisos>`\n"
                "`>wsys punicoes` — lista\n"
                "`>wsys confirm ...` — quem pode confirmar\n"
                "`>wsys immune ...` — cargos imunes\n"
                "`>wsys aplicar ...` — quem pode aplicar\n"
                "`>warn2 @user <motivo>` — aplicar warn\n"
                "`>warns2 @user` — ver warns"
            ),
            inline=False)

        await ctx.send(embed=embed)

    @wsys.command(name="on")
    @commands.has_permissions(administrator=True)
    async def wsys_on(self, ctx):
        await WarnManager.update(ctx.guild.id, "enabled", True)
        await ctx.send(embed=embed_success("✅ Sistema ativado."))

    @wsys.command(name="off")
    @commands.has_permissions(administrator=True)
    async def wsys_off(self, ctx):
        await WarnManager.update(ctx.guild.id, "enabled", False)
        await ctx.send(embed=embed_success("✅ Sistema desativado."))

    @wsys.command(name="autoapply")
    @commands.has_permissions(administrator=True)
    async def wsys_autoapply(self, ctx, state: str):
        enabled = state.lower() in ("on", "true", "1", "sim")
        await WarnManager.update(ctx.guild.id, "auto_apply", enabled)
        msg = ("aplica direto (sem confirmação)"
               if enabled else "pede confirmação")
        await ctx.send(embed=embed_success(f"✅ Auto-apply: {msg}"))

    @wsys.command(name="log")
    @commands.has_permissions(administrator=True)
    async def wsys_log(self, ctx, canal: discord.TextChannel):
        await WarnManager.update(ctx.guild.id, "log_canal", canal.id)
        await ctx.send(embed=embed_success(f"✅ Log em {canal.mention}"))

    @wsys.command(name="janela")
    @commands.has_permissions(administrator=True)
    async def wsys_janela(self, ctx, segundos: int):
        if segundos < 1 or segundos > 3600:
            return await ctx.send(embed=embed_error(
                "❌ Entre 1 e 3600 segundos."))
        await WarnManager.update(ctx.guild.id, "janela_tempo", segundos)
        await ctx.send(embed=embed_success(
            f"✅ Janela: `{segundos}s`"))

    @wsys.command(name="detect")
    @commands.has_permissions(administrator=True)
    async def wsys_detect(self, ctx, regra: str, state: str):
        """Liga/desliga uma detecção."""
        cfg = await WarnManager.get_config(ctx.guild.id)
        key = f"detect_{regra}"
        if key not in cfg:
            return await ctx.send(embed=embed_error(
                f"❌ Regra `{regra}` não existe."))
        enabled = state.lower() in ("on", "true", "1", "sim")
        await WarnManager.update(ctx.guild.id, key, enabled)
        await ctx.send(embed=embed_success(
            f"✅ `{regra}` {'ON' if enabled else 'OFF'}"))

    @wsys.command(name="limite")
    @commands.has_permissions(administrator=True)
    async def wsys_limite(self, ctx, regra: str, valor: int):
        """Define o limite de uma detecção."""
        cfg = await WarnManager.get_config(ctx.guild.id)
        key = f"limite_{regra}"
        if key not in cfg:
            return await ctx.send(embed=embed_error(
                f"❌ Limite `{regra}` não existe."))
        await WarnManager.update(ctx.guild.id, key, valor)
        await ctx.send(embed=embed_success(
            f"✅ Limite `{regra}`: `{valor}`"))

    @wsys.command(name="addpunish")
    @commands.has_permissions(administrator=True)
    async def wsys_addpunish(self, ctx, avisos: int, acao: str,
                              duracao: int = 0):
        """Adiciona uma punição progressiva."""
        if acao not in ("timeout", "kick", "ban", "strip"):
            return await ctx.send(embed=embed_error(
                "❌ Ações: `timeout`, `kick`, `ban`, `strip`"))
        if avisos < 1:
            return await ctx.send(embed=embed_error(
                "❌ Avisos deve ser >= 1."))
        cfg = await WarnManager.get_config(ctx.guild.id)
        punicoes = list(cfg.get("punicoes", []))
        # Remove se já existe com mesmo número.
        punicoes = [p for p in punicoes if p.get("avisos") != avisos]
        # Adiciona.
        punicoes.append({
            "avisos": avisos,
            "acao": acao,
            "duracao_min": duracao,
        })
        # Ordena.
        punicoes.sort(key=lambda p: p["avisos"])
        await WarnManager.update(ctx.guild.id, "punicoes", punicoes)
        dur_str = f" ({duracao}min)" if duracao else ""
        await ctx.send(embed=embed_success(
            f"✅ Adicionado: `{avisos}` avisos → `{acao}`{dur_str}"))

    @wsys.command(name="removepunish")
    @commands.has_permissions(administrator=True)
    async def wsys_removepunish(self, ctx, avisos: int):
        """Remove uma punição."""
        cfg = await WarnManager.get_config(ctx.guild.id)
        punicoes = [p for p in cfg.get("punicoes", [])
                    if p.get("avisos") != avisos]
        await WarnManager.update(ctx.guild.id, "punicoes", punicoes)
        await ctx.send(embed=embed_success(
            f"✅ Punição com `{avisos}` avisos removida."))

    @wsys.command(name="punicoes")
    @commands.has_permissions(administrator=True)
    async def wsys_punicoes(self, ctx):
        """Lista todas as punições configuradas."""
        cfg = await WarnManager.get_config(ctx.guild.id)
        punicoes = cfg.get("punicoes", [])
        if not punicoes:
            return await ctx.send(embed=embed_info(
                "📋 Nenhuma punição configurada."))
        embed = discord.Embed(
            title="⚖️ Punições Progressivas",
            color=discord.Color.orange(),
            timestamp=_now())
        for p in punicoes:
            embed.add_field(
                name=f"{p['avisos']} avisos",
                value=self._descrever_punicao(p),
                inline=True)
        await ctx.send(embed=embed)

    # ═══════════════════════════════════════════════════════════
    # Subcomandos: confirm
    # ═══════════════════════════════════════════════════════════

    @wsys.group(name="confirm", invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def wsys_confirm(self, ctx):
        await ctx.send(embed=embed_info(
            "Use: `>wsys confirm addrole/removerole/adduser/removeuser/"
            "exceptrole/exceptuser @x`"))

    @wsys_confirm.command(name="addrole")
    @commands.has_permissions(administrator=True)
    async def wsys_confirm_addrole(self, ctx, role: discord.Role):
        cfg = await WarnManager.get_config(ctx.guild.id)
        roles = list(cfg.get("cargos_confirmar", []))
        if role.id not in roles:
            roles.append(role.id)
            await WarnManager.update(ctx.guild.id, "cargos_confirmar", roles)
        await ctx.send(embed=embed_success(
            f"✅ {role.mention} pode confirmar."))

    @wsys_confirm.command(name="removerole")
    @commands.has_permissions(administrator=True)
    async def wsys_confirm_removerole(self, ctx, role: discord.Role):
        cfg = await WarnManager.get_config(ctx.guild.id)
        roles = [r for r in cfg.get("cargos_confirmar", [])
                 if r != role.id]
        await WarnManager.update(ctx.guild.id, "cargos_confirmar", roles)
        await ctx.send(embed=embed_success(
            f"✅ {role.mention} NÃO pode confirmar."))

    @wsys_confirm.command(name="adduser")
    @commands.has_permissions(administrator=True)
    async def wsys_confirm_adduser(self, ctx, user: discord.Member):
        cfg = await WarnManager.get_config(ctx.guild.id)
        users = list(cfg.get("users_confirmar", []))
        if user.id not in users:
            users.append(user.id)
            await WarnManager.update(ctx.guild.id, "users_confirmar", users)
        await ctx.send(embed=embed_success(
            f"✅ {user.mention} pode confirmar."))

    @wsys_confirm.command(name="removeuser")
    @commands.has_permissions(administrator=True)
    async def wsys_confirm_removeuser(self, ctx, user: discord.Member):
        cfg = await WarnManager.get_config(ctx.guild.id)
        users = [u for u in cfg.get("users_confirmar", [])
                 if u != user.id]
        await WarnManager.update(ctx.guild.id, "users_confirmar", users)
        await ctx.send(embed=embed_success(
            f"✅ {user.mention} NÃO pode confirmar."))

    @wsys_confirm.command(name="exceptrole")
    @commands.has_permissions(administrator=True)
    async def wsys_confirm_exceptrole(self, ctx, role: discord.Role,
                                        user: discord.Member):
        cfg = await WarnManager.get_config(ctx.guild.id)
        exceto = dict(cfg.get("cargos_confirmar_exceto", {}))
        lista = list(exceto.get(str(role.id), []))
        if user.id not in lista:
            lista.append(user.id)
        exceto[str(role.id)] = lista
        await WarnManager.update(ctx.guild.id, "cargos_confirmar_exceto", exceto)
        await ctx.send(embed=embed_success(
            f"✅ {role.mention} pode confirmar, EXCETO {user.mention}."))

    @wsys_confirm.command(name="exceptuser")
    @commands.has_permissions(administrator=True)
    async def wsys_confirm_exceptuser(self, ctx, admin: discord.Member,
                                        user: discord.Member):
        cfg = await WarnManager.get_config(ctx.guild.id)
        exceto = dict(cfg.get("users_confirmar_exceto", {}))
        lista = list(exceto.get(str(admin.id), []))
        if user.id not in lista:
            lista.append(user.id)
        exceto[str(admin.id)] = lista
        await WarnManager.update(ctx.guild.id, "users_confirmar_exceto", exceto)
        await ctx.send(embed=embed_success(
            f"✅ {admin.mention} pode confirmar, EXCETO {user.mention}."))

    # ═══════════════════════════════════════════════════════════
    # Subcomandos: immune
    # ═══════════════════════════════════════════════════════════

    @wsys.group(name="immune", invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def wsys_immune(self, ctx):
        await ctx.send(embed=embed_info(
            "Use: `>wsys immune addrole/removerole/exceptrole @x`"))

    @wsys_immune.command(name="addrole")
    @commands.has_permissions(administrator=True)
    async def wsys_immune_addrole(self, ctx, role: discord.Role):
        cfg = await WarnManager.get_config(ctx.guild.id)
        imunes = list(cfg.get("cargos_imunes", []))
        if role.id not in imunes:
            imunes.append(role.id)
            await WarnManager.update(ctx.guild.id, "cargos_imunes", imunes)
        await ctx.send(embed=embed_success(
            f"✅ {role.mention} é imune a warns."))

    @wsys_immune.command(name="removerole")
    @commands.has_permissions(administrator=True)
    async def wsys_immune_removerole(self, ctx, role: discord.Role):
        cfg = await WarnManager.get_config(ctx.guild.id)
        imunes = [r for r in cfg.get("cargos_imunes", []) if r != role.id]
        await WarnManager.update(ctx.guild.id, "cargos_imunes", imunes)
        await ctx.send(embed=embed_success(
            f"✅ {role.mention} NÃO é mais imune."))

    @wsys_immune.command(name="exceptrole")
    @commands.has_permissions(administrator=True)
    async def wsys_immune_exceptrole(self, ctx, role: discord.Role,
                                       user: discord.Member):
        cfg = await WarnManager.get_config(ctx.guild.id)
        exceto = dict(cfg.get("cargos_imunes_exceto", {}))
        lista = list(exceto.get(str(role.id), []))
        if user.id not in lista:
            lista.append(user.id)
        exceto[str(role.id)] = lista
        await WarnManager.update(ctx.guild.id, "cargos_imunes_exceto", exceto)
        await ctx.send(embed=embed_success(
            f"✅ {role.mention} é imune, EXCETO punições de {user.mention}."))

    # ═══════════════════════════════════════════════════════════
    # Subcomandos: aplicar
    # ═══════════════════════════════════════════════════════════

    @wsys.group(name="aplicar", invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def wsys_aplicar(self, ctx):
        await ctx.send(embed=embed_info(
            "Use: `>wsys aplicar addrole/removerole/adduser/removeuser @x`"))

    @wsys_aplicar.command(name="addrole")
    @commands.has_permissions(administrator=True)
    async def wsys_aplicar_addrole(self, ctx, role: discord.Role):
        cfg = await WarnManager.get_config(ctx.guild.id)
        roles = list(cfg.get("cargos_aplicar", []))
        if role.id not in roles:
            roles.append(role.id)
            await WarnManager.update(ctx.guild.id, "cargos_aplicar", roles)
        await ctx.send(embed=embed_success(
            f"✅ {role.mention} pode aplicar warns."))

    @wsys_aplicar.command(name="removerole")
    @commands.has_permissions(administrator=True)
    async def wsys_aplicar_removerole(self, ctx, role: discord.Role):
        cfg = await WarnManager.get_config(ctx.guild.id)
        roles = [r for r in cfg.get("cargos_aplicar", []) if r != role.id]
        await WarnManager.update(ctx.guild.id, "cargos_aplicar", roles)
        await ctx.send(embed=embed_success(
            f"✅ {role.mention} NÃO pode mais aplicar warns."))

    @wsys_aplicar.command(name="adduser")
    @commands.has_permissions(administrator=True)
    async def wsys_aplicar_adduser(self, ctx, user: discord.Member):
        cfg = await WarnManager.get_config(ctx.guild.id)
        users = list(cfg.get("users_aplicar", []))
        if user.id not in users:
            users.append(user.id)
            await WarnManager.update(ctx.guild.id, "users_aplicar", users)
        await ctx.send(embed=embed_success(
            f"✅ {user.mention} pode aplicar warns."))

    @wsys_aplicar.command(name="removeuser")
    @commands.has_permissions(administrator=True)
    async def wsys_aplicar_removeuser(self, ctx, user: discord.Member):
        cfg = await WarnManager.get_config(ctx.guild.id)
        users = [u for u in cfg.get("users_aplicar", []) if u != user.id]
        await WarnManager.update(ctx.guild.id, "users_aplicar", users)
        await ctx.send(embed=embed_success(
            f"✅ {user.mention} NÃO pode mais aplicar warns."))

    # ============================================================
    # COMANDO: >warn2 (aplicar warn manual)
    # ============================================================

    @commands.command(name="warn2")
    async def warn2(self, ctx, user: discord.Member, *,
                     motivo: str = "Sem motivo informado"):
        """Aplica um aviso em um usuário."""
        cfg = await WarnManager.get_config(ctx.guild.id)
        # Verifica permissão.
        if not _pode_aplicar(cfg, ctx.author):
            return await ctx.send(embed=embed_error(
                "❌ Você não tem permissão para aplicar avisos."))

        # User é imune?
        if _cargo_imune(cfg, user, ctx.author.id):
            return await ctx.send(embed=embed_warning(
                f"⚠️ {user.mention} é imune a avisos."))

        # Detecta sufixo "auto" (aplica sem confirmação).
        auto = motivo.lower().endswith(" auto")
        if auto:
            motivo = motivo[:-5].strip()

        # Aplica (aviso no canal do comando, não em DM).
        warn_id = await self.aplicar_warn(
            guild=ctx.guild,
            user=user,
            regra="manual_staff",
            motivo=motivo,
            aplicado_por=ctx.author,
            canal=ctx.channel,
        )

        if warn_id:
            await ctx.send(embed=embed_success(
                f"✅ Aviso aplicado em {user.mention}.\n"
                f"**ID:** `{warn_id[:8]}`"))
        else:
            await ctx.send(embed=embed_error(
                "❌ Falha ao aplicar o aviso."))

    # ============================================================
    # COMANDO: >warnbatch (warn em lote)
    # ============================================================

    @commands.command(name="warnbatch", aliases=["warnlote"])
    @commands.has_permissions(administrator=True)
    async def warn_batch(self, ctx, *members: discord.Member):
        """
        Aplica warn em VÁRIOS membros em paralelo (útil em raid).
        Uso: >warnbatch @user1 @user2 @user3 ...
        """
        if not members:
            return await ctx.send(embed=embed_error(
                "❌ Use: `>warnbatch @u1 @u2 @u3 ...`"))

        if len(members) > 20:
            return await ctx.send(embed=embed_error(
                "❌ Máximo 20 membros por vez."))

        msg = await ctx.send(embed=embed_info(
            f"⏳ Aplicando warn em `{len(members)}` membros..."))

        # Monta os jobs.
        jobs = [
            {"user": m, "regra": "manual_staff",
             "motivo": f"Warn em lote por {ctx.author}"}
            for m in members
        ]

        # Aplica em paralelo.
        stats = await self.aplicar_warn_lote(
            ctx.guild, jobs, aplicado_por=ctx.author, canal=ctx.channel)

        await msg.edit(embed=embed_success(
            f"✅ `{stats['ok']}` warns aplicados.\n"
            f"⏭️ Ignorados (imunes): `{stats['skipped']}`\n"
            f"❌ Falhas: `{stats['fail']}`"))

    # ============================================================
    # COMANDOS DE CONSULTA
    # ============================================================

    @commands.command(name="warns2")
    @commands.has_permissions(kick_members=True)
    async def warns2(self, ctx, user: discord.Member):
        """Lista os avisos de um usuário."""
        db = await get_connection_async()
        # Materializa no máximo 20 resultados para preservar o limite do comando.
        warns = await db["warn_system_warns"].find(
            {"guild_id": ctx.guild.id, "user_id": user.id, "ativo": True}
        ).sort("timestamp", -1).limit(20).to_list(length=20)

        if not warns:
            return await ctx.send(embed=embed_info(
                f"📋 {user.mention} não tem avisos."))

        embed = discord.Embed(
            title=f"⚠️ Avisos de {user.display_name}",
            description=f"Total: **{len(warns)}** avisos",
            color=discord.Color.orange(),
            timestamp=_now())
        embed.set_thumbnail(url=user.display_avatar.url)

        for w in warns[:10]:
            regra = w.get("regra", "?")
            regra_label = REGRAS_DISPONIVEIS.get(regra, regra)
            regra_emoji = REGRAS_EMOJI.get(regra, "⚠️")
            ts = w.get("timestamp", _now())
            ts_str = ts.strftime("%d/%m %H:%M") if hasattr(ts, "strftime") else str(ts)
            embed.add_field(
                name=f"{regra_emoji} {regra_label} — {ts_str}",
                value=(
                    f"**ID:** `{str(w['_id'])[:8]}`\n"
                    f"**Por:** <@{w.get('aplicado_por')}>\n"
                    f"**Motivo:** {w.get('motivo', '—')[:200]}"
                ),
                inline=False)

        await ctx.send(embed=embed)

    @commands.command(name="warninfo")
    @commands.has_permissions(kick_members=True)
    async def warninfo(self, ctx, warn_id: str):
        """Mostra detalhes de um aviso específico pelo ID."""
        from utils import safe_object_id
        oid = safe_object_id(warn_id)
        if not oid:
            return await ctx.send(embed=embed_error(
                "❌ ID inválido. Use `>warns2 @user` pra pegar o ID."))

        db = await get_connection_async()
        warn = await db["warn_system_warns"].find_one(
            {"_id": oid, "guild_id": ctx.guild.id})
        if not warn:
            return await ctx.send(embed=embed_error(
                "❌ Aviso não encontrado."))

        regra = warn.get("regra", "?")
        regra_label = REGRAS_DISPONIVEIS.get(regra, regra)
        regra_emoji = REGRAS_EMOJI.get(regra, "⚠️")
        ts = warn.get("timestamp", _now())

        embed = discord.Embed(
            title=f"{regra_emoji} Detalhes do Aviso",
            description=f"**ID:** `{warn_id}`",
            color=discord.Color.orange(),
            timestamp=ts if isinstance(ts, datetime) else _now())
        embed.add_field(
            name="👤 Usuário",
            value=f"<@{warn.get('user_id')}>",
            inline=True)
        embed.add_field(
            name="📋 Regra",
            value=f"`{regra_label}`",
            inline=True)
        embed.add_field(
            name="🛡️ Aplicado por",
            value=f"<@{warn.get('aplicado_por')}>",
            inline=True)
        embed.add_field(
            name="📝 Motivo",
            value=warn.get("motivo", "—"),
            inline=False)
        if warn.get("evidencia"):
            embed.add_field(
                name="📎 Evidência",
                value=warn["evidencia"],
                inline=False)
        embed.add_field(
            name="📅 Data",
            value=(f"<t:{int(ts.timestamp())}:F>"
                   if hasattr(ts, "timestamp") else str(ts)),
            inline=False)
        embed.add_field(
            name="Status",
            value="✅ Ativo" if warn.get("ativo") else "❌ Removido",
            inline=True)

        await ctx.send(embed=embed)

    @commands.command(name="clearwarns2")
    @commands.has_permissions(administrator=True)
    async def clearwarns2(self, ctx, user: discord.Member):
        """Remove todos os avisos de um usuário."""
        db = await get_connection_async()
        r = await db["warn_system_warns"].update_many(
            {"guild_id": ctx.guild.id, "user_id": user.id, "ativo": True},
            {"$set": {"ativo": False}})
        await ctx.send(embed=embed_success(
            f"✅ {r.modified_count} avisos removidos de {user.mention}."))

    @commands.command(name="unwarn")
    @commands.has_permissions(administrator=True)
    async def unwarn(self, ctx, warn_id: str):
        """Remove um aviso específico pelo ID."""
        from utils import safe_object_id
        oid = safe_object_id(warn_id)
        if not oid:
            return await ctx.send(embed=embed_error("❌ ID inválido."))
        db = await get_connection_async()
        r = await db["warn_system_warns"].update_one(
            {"_id": oid, "guild_id": ctx.guild.id},
            {"$set": {"ativo": False}})
        if r.modified_count > 0:
            await ctx.send(embed=embed_success(
                f"✅ Aviso `{warn_id[:8]}` removido."))
        else:
            await ctx.send(embed=embed_error("❌ Aviso não encontrado."))


async def setup(bot):
    """Chamado pelo main.py."""
    if bot.get_cog("WarnSystemCommands") is None:
        await bot.add_cog(WarnSystemCommands(bot))