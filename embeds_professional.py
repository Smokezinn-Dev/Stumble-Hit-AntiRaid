# ============================================================
# EMBEDS_PROFESSIONAL.PY - v1.0
# LOGS PROFISSIONAIS COM DESIGN LIMPO
# ============================================================
# Este arquivo cria embeds "bonitos" e padronizados pra logs.
# A ideia é que todo log do bot tenha:
#   - Avatar do alvo à direita (thumbnail)
#   - Texto natural (não robótico)
#   - Executor sempre visível
#   - Cores semânticas (vermelho=grave, verde=ok, etc)
#   - Footer consistente
# ============================================================

from datetime import datetime, timezone
from typing import Optional, Union, List
import discord


def _now() -> datetime:
    """Retorna data/hora atual em UTC (fuso mundial)."""
    return datetime.now(timezone.utc)


# ============================================================
# PALETA DE CORES (por severidade)
# ============================================================
# Cores em hexadecimal (0xRRGGBB). Ex: 0xC0392B = vermelho escuro.

class LogColors:
    CRITICAL = 0xA94442   # vermelho escuro → perigo máximo
    HIGH     = 0xB94A48   # vermelho → perigo alto
    MEDIUM   = 0xC58B3C   # laranja → atenção
    LOW      = 0xB89A55   # amarelo → aviso leve
    SUCCESS  = 0x2E8B70   # verde → tudo certo
    INFO     = 0x526F9D   # azul → informação
    PROTECT  = 0x65708F   # roxo → proteção ativa
    NEUTRAL  = 0x465268   # cinza → neutro

    @classmethod
    def from_severity(cls, sev: str) -> int:
        """
        Converte o nome da severidade em cor.
        Ex: "critical" → vermelho escuro.
        """
        return {
            "critical": cls.CRITICAL,
            "high":     cls.HIGH,
            "medium":   cls.MEDIUM,
            "low":      cls.LOW,
        }.get((sev or "medium").lower(), cls.NEUTRAL)


# ============================================================
# BUILDER DE LOG PROFISSIONAL
# ============================================================
# Essa classe é tipo um "construtor de LEGO". Você vai
# adicionando peças (título, descrição, alvo, executor, campos)
# e no final chama .build() pra montar o embed final.

class ProfessionalLog:
    """
    Builder de embeds com design profissional para logs.

    Uso:
        log = ProfessionalLog(
            title="Tentativa de Nuke Bloqueada",
            description="Um membro tentou deletar canais em massa.",
            severity="critical")
        log.set_target(member)         # avatar à direita
        log.set_executor(moderator)    # quem fez
        log.add_field("Ação", "channel_delete")
        log.add_field("Canais", "5")
        embed = log.build()            # monta o embed pronto
    """

    def __init__(self, title: str, description: str = "",
                 severity: str = "medium",
                 footer: str = "Stumble Hit AntiRaid • Registro de moderação"):
        """
        Args:
            title: título do embed
            description: texto principal
            severity: "critical", "high", "medium", "low" (define a cor)
            footer: rodapé do embed
        """
        self.title = title
        self.description = description
        self.severity = severity
        self.footer = footer
        self.fields: List[dict] = []          # campos adicionados
        self._target: Optional[tuple] = None  # (member, label)
        self._executor: Optional[tuple] = None

    def set_target(self, member: Optional[discord.Member],
                    label: str = "Alvo"):
        """Define quem é o 'alvo' (avatar aparece à direita)."""
        self._target = (member, label)
        return self  # permite encadear: log.set_target(x).add_field(...)

    def set_executor(self, member: Optional[discord.Member],
                      label: str = "Executado por"):
        """Define quem executou a ação."""
        self._executor = (member, label)
        return self

    def add_field(self, name: str, value: str, inline: bool = True):
        """
        Adiciona um campo ao embed.
        inline=True → fica ao lado de outros inline
        inline=False → ocupa a linha inteira
        """
        self.fields.append({"name": name, "value": str(value),
                            "inline": inline})
        return self

    def build(self) -> discord.Embed:
        """Monta o embed final pronto pra ser enviado."""
        # Cria o embed vazio com título, descrição, cor e timestamp.
        embed = discord.Embed(
            title=self.title,
            description=self.description or None,
            color=LogColors.from_severity(self.severity))

        # Se tem alvo, coloca o avatar como thumbnail (canto direito).
        if self._target:
            member, label = self._target
            if member is not None:
                # display_avatar = avatar do servidor ou global
                avatar = getattr(member, "display_avatar", None)
                if avatar:
                    embed.set_thumbnail(url=avatar.url)
                # Adiciona campo com menção + ID.
                embed.add_field(
                    name=label,
                    value=f"{member.mention}\n`{member.id}`",
                    inline=True)

        # Se tem executor, adiciona campo.
        if self._executor:
            member, label = self._executor
            if member is not None:
                embed.add_field(
                    name=label,
                    value=f"{member.mention}\n`{member.id}`",
                    inline=True)

        # Adiciona todos os campos acumulados.
        for f in self.fields:
            embed.add_field(name=f["name"], value=f["value"],
                            inline=f["inline"])

        # Aplica o footer.
        embed.set_footer(text=self.footer)
        return embed


# ============================================================
# HELPERS DE FORMATAÇÃO
# ============================================================
# Funções pequenas que formatam dados pra exibir em embeds.

def fmt_user(user: Optional[Union[discord.Member, discord.User]]) -> str:
    """
    Formata um usuário como "menção + ID".
    Ex: "@Fulano\n`123456789`"
    """
    if not user:
        return "—"
    return f"{user.mention}\n`{user.id}`"


def fmt_duration(seconds: int) -> str:
    """
    Formata segundos em texto humano.
    Ex: 45 → "45s", 120 → "2min", 7200 → "2h", 172800 → "2d"
    """
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}min"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def fmt_reason(reason: Optional[str], max_len: int = 400) -> str:
    """
    Formata o motivo, cortando se for muito grande.
    Ex: motivo com 1000 chars → corta em 400 + "..."
    """
    if not reason:
        return "—"
    r = str(reason)
    return r if len(r) <= max_len else r[:max_len] + "..."