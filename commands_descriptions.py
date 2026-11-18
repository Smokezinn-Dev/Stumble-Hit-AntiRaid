# ============================================================
# COMMANDS_DESCRIPTIONS.PY - v1.0
# SISTEMA DE DESCRIÇÕES DE COMANDOS
# ============================================================
# 📌 O QUE ESSE ARQUIVO FAZ?
# Substitui o help padrão por um help RICO com descrições
# bonitas de cada comando.
#
# Além disso, guarda um dicionário GIGANTE com:
#   • Descrição
#   • Como usar
#   • Exemplos
#   • Categoria
#   • Permissões necessárias
#   • Aliases
#   • Cooldown
#
# Assim, quando alguém faz ">help ban", aparece tudo isso.
# ============================================================

import discord
from discord.ext import commands
from datetime import datetime, timezone
from typing import Optional


def _now() -> datetime:
    """Hora atual em UTC."""
    return datetime.now(timezone.utc)


# ============================================================
# DICIONÁRIO DE DESCRIÇÕES
# ============================================================
# Cada comando tem:
#   descricao    = texto explicativo
#   uso          = formato de uso
#   exemplos     = lista de exemplos
#   categoria    = mod, purge, etc
#   permissoes   = permissão do Discord necessária
#   aliases      = outros nomes
#   cooldown     = tempo entre usos
#   observacoes  = notas extras

DESCRICOES = {
    # ─── Moderação básica ───
    "kick": {
        "descricao": "Expulsa um membro do servidor. O membro pode "
                      "voltar com um novo convite.",
        "uso": ">kick @membro [motivo]",
        "exemplos": [
            ">kick @Fulano Comportamento inadequado",
            ">kick @Beltrano Spam em canais",
        ],
        "categoria": "mod",
        "permissoes": "kick_members",
        "cooldown": "3s por usuário",
    },
    "ban": {
        "descricao": "Bane um membro permanentemente. O membro não pode "
                      "voltar sem ser desbanido.",
        "uso": ">ban @membro [motivo]",
        "exemplos": [
            ">ban @Fulano Raid detectada",
            ">ban @Beltrano Ofensas graves",
        ],
        "categoria": "mod",
        "permissoes": "ban_members",
        "cooldown": "3s por usuário",
    },
    "softban": {
        "descricao": "Bane e desbane imediatamente. Serve para apagar "
                      "as últimas mensagens do usuário sem deixá-lo "
                      "banido permanentemente.",
        "uso": ">softban @membro [motivo]",
        "exemplos": [">softban @Fulano Limpar spam"],
        "categoria": "mod",
        "permissoes": "ban_members",
    },
    "tempban": {
        "descricao": "Bane temporariamente. Após a duração, o membro "
                      "é desbanido automaticamente.",
        "uso": ">tempban @membro <duração> [motivo]",
        "exemplos": [
            ">tempban @Fulano 1h Motivo",
            ">tempban @Beltrano 1d Comportamento",
        ],
        "categoria": "mod",
        "permissoes": "ban_members",
        "observacoes": "Durações: 10m, 1h, 1d, 1w",
    },
    "unban": {
        "descricao": "Remove o banimento de um usuário pelo ID.",
        "uso": ">unban <user_id> [motivo]",
        "exemplos": [">unban 123456789012345678 Apelação aceita"],
        "categoria": "mod",
        "permissoes": "ban_members",
    },
    "massban": {
        "descricao": "Bane vários usuários de uma vez (máx 10).",
        "uso": ">massban <id1> <id2> <id3> ...",
        "exemplos": [">massban 123456 789012 345678"],
        "categoria": "mod",
        "permissoes": "ban_members",
    },
    "timeout": {
        "descricao": "Silencia um membro por um período. Ele não pode "
                      "enviar mensagens, reagir ou entrar em voz.",
        "uso": ">timeout @membro <duração> [motivo]",
        "exemplos": [
            ">timeout @Fulano 10m Spam",
            ">timeout @Beltrano 1h Briga",
        ],
        "categoria": "mod",
        "permissoes": "moderate_members",
        "aliases": ["mute"],
        "observacoes": "Durações: 10m, 1h, 1d, 1w",
    },
    "untimeout": {
        "descricao": "Remove o timeout de um membro imediatamente.",
        "uso": ">untimeout @membro",
        "exemplos": [">untimeout @Fulano"],
        "categoria": "mod",
        "permissoes": "moderate_members",
        "aliases": ["unmute"],
    },
    "warn": {
        "descricao": "Registra uma advertência no histórico do membro. "
                      "Após 3 warns, ele recebe strike automático.",
        "uso": ">warn @membro [motivo]",
        "exemplos": [">warn @Fulano Linguagem inadequada"],
        "categoria": "mod",
        "permissoes": "kick_members",
    },
    "warns": {
        "descricao": "Lista os warnes de um membro.",
        "uso": ">warns @membro",
        "exemplos": [">warns @Fulano"],
        "categoria": "mod",
        "permissoes": "kick_members",
    },
    "clearwarns": {
        "descricao": "Remove todos os warns de um membro.",
        "uso": ">clearwarns @membro",
        "exemplos": [">clearwarns @Fulano"],
        "categoria": "mod",
        "permissoes": "kick_members",
    },
    "strike": {
        "descricao": "Aplica strike em um membro. Silencia por 1 hora "
                      "automaticamente.",
        "uso": ">strike @membro [motivo]",
        "exemplos": [">strike @Fulano Comportamento repetido"],
        "categoria": "mod",
        "permissoes": "ban_members",
    },
    "unstrike": {
        "descricao": "Remove o strike de um membro.",
        "uso": ">unstrike @membro",
        "exemplos": [">unstrike @Fulano"],
        "categoria": "mod",
        "permissoes": "ban_members",
    },
    "silentban": {
        "descricao": "Bane um membro sem enviar mensagem no canal.",
        "uso": ">silentban @membro [motivo]",
        "exemplos": [">silentban @Fulano Comportamento"],
        "categoria": "mod",
        "permissoes": "ban_members",
    },
    "silentkick": {
        "descricao": "Expulsa um membro sem enviar mensagem no canal.",
        "uso": ">silentkick @membro [motivo]",
        "exemplos": [">silentkick @Fulano Comportamento"],
        "categoria": "mod",
        "permissoes": "kick_members",
    },
    "jail": {
        "descricao": "Prende um membro removendo todos os cargos "
                      "(salvando pra depois). Aplica timeout de 24h.",
        "uso": ">jail @membro [motivo]",
        "exemplos": [">jail @Fulano Comportamento"],
        "categoria": "mod",
        "permissoes": "moderate_members",
    },
    "unjail": {
        "descricao": "Solta um membro, restaurando todos os cargos que "
                      "ele tinha antes.",
        "uso": ">unjail @membro",
        "exemplos": [">unjail @Fulano"],
        "categoria": "mod",
        "permissoes": "moderate_members",
    },
    "strip": {
        "descricao": "Remove TODOS os cargos de um membro (salva pra "
                      "poder restaurar).",
        "uso": ">strip @membro [motivo]",
        "exemplos": [">strip @Fulano Abuso de poder"],
        "categoria": "mod",
        "permissoes": "administrator",
    },
    "restore": {
        "descricao": "Restaura os cargos de um membro que foi stripado "
                      "ou preso.",
        "uso": ">restore @membro",
        "exemplos": [">restore @Fulano"],
        "categoria": "mod",
        "permissoes": "administrator",
    },

    # ─── Purge ───
    "purge": {
        "descricao": "Apaga as últimas N mensagens do canal.",
        "uso": ">purge <número>",
        "exemplos": [">purge 50", ">purge 100"],
        "categoria": "purge",
        "permissoes": "manage_messages",
        "aliases": ["limpar", "clear"],
        "observacoes": "Máximo: 100 mensagens por vez",
    },
    "purgebots": {
        "descricao": "Apaga mensagens de bots no canal.",
        "uso": ">purgebots <número>",
        "exemplos": [">purgebots 50"],
        "categoria": "purge",
        "permissoes": "manage_messages",
    },
    "purgechannel": {
        "descricao": "Limpa TODO o canal (pede confirmação).",
        "uso": ">purgechannel",
        "exemplos": [">purgechannel"],
        "categoria": "purge",
        "permissoes": "manage_channels",
        "observacoes": "Requer confirmação 'CONFIRMAR' em 15s",
    },
    "nuke": {
        "descricao": "Deleta e recria o canal vazio (nuke).",
        "uso": ">nuke",
        "exemplos": [">nuke"],
        "categoria": "purge",
        "permissoes": "administrator",
        "observacoes": "⚠️ Ação destrutiva! Pede confirmação",
    },
    "purgelink": {
        "descricao": "Apaga mensagens que contêm links.",
        "uso": ">purgelink <número>",
        "exemplos": [">purgelink 50"],
        "categoria": "purge",
        "permissoes": "manage_messages",
    },
    "purgeuser": {
        "descricao": "Apaga mensagens de um usuário específico.",
        "uso": ">purgeuser @user <número>",
        "exemplos": [">purgeuser @Fulano 50"],
        "categoria": "purge",
        "permissoes": "manage_messages",
    },
    "purgemulti": {
        "descricao": "Apaga mensagens em vários canais de uma vez.",
        "uso": ">purgemulti <n> #canal1 #canal2 ...",
        "exemplos": [">purgemulti 20 #geral #spam"],
        "categoria": "purge",
        "permissoes": "administrator",
    },
    "snipe": {
        "descricao": "Mostra a última mensagem deletada do canal.",
        "uso": ">snipe [índice]",
        "exemplos": [">snipe", ">snipe 3"],
        "categoria": "message",
        "permissoes": "manage_messages",
    },
    "editsnipe": {
        "descricao": "Mostra a última mensagem editada do canal.",
        "uso": ">editsnipe [índice]",
        "exemplos": [">editsnipe"],
        "categoria": "message",
        "permissoes": "manage_messages",
    },

    # ─── Canais ───
    "lock": {
        "descricao": "Trava o canal (ninguém pode enviar mensagens).",
        "uso": ">lock [#canal]",
        "exemplos": [">lock", ">lock #geral"],
        "categoria": "channel",
        "permissoes": "manage_channels",
    },
    "unlock": {
        "descricao": "Destranca o canal.",
        "uso": ">unlock [#canal]",
        "exemplos": [">unlock #geral"],
        "categoria": "channel",
        "permissoes": "manage_channels",
    },
    "lockdown": {
        "descricao": "Trava TODOS os canais de texto do servidor. "
                      "Use em emergência de raid.",
        "uso": ">lockdown",
        "exemplos": [">lockdown"],
        "categoria": "channel",
        "permissoes": "administrator",
    },
    "templock": {
        "descricao": "Trava um canal temporariamente.",
        "uso": ">templock <duração> [#canal]",
        "exemplos": [">templock 10m #geral", ">templock 1h"],
        "categoria": "channel",
        "permissoes": "manage_channels",
        "observacoes": "Durações: 10s, 5m, 1h, 1d (máx 7d)",
    },
    "slowmode": {
        "descricao": "Define o modo lento do canal.",
        "uso": ">slowmode <segundos>",
        "exemplos": [">slowmode 5", ">slowmode 0"],
        "categoria": "channel",
        "permissoes": "manage_channels",
        "observacoes": "0 = desliga | máx 21600s (6h)",
    },

    # ─── Cargos ───
    "addrole": {
        "descricao": "Adiciona um cargo a um membro.",
        "uso": ">addrole @membro @cargo [motivo]",
        "exemplos": [">addrole @Fulano @Staff Promoção"],
        "categoria": "role",
        "permissoes": "manage_roles",
    },
    "removerole": {
        "descricao": "Remove um cargo de um membro.",
        "uso": ">removerole @membro @cargo [motivo]",
        "exemplos": [">removerole @Fulano @Staff Demissão"],
        "categoria": "role",
        "permissoes": "manage_roles",
    },
    "temprole": {
        "descricao": "Dá um cargo temporário (removido automaticamente "
                      "ao expirar).",
        "uso": ">temprole @membro @cargo <duração> [motivo]",
        "exemplos": [">temprole @Fulano @Staff 1d Teste"],
        "categoria": "role",
        "permissoes": "manage_roles",
    },
    "createrole": {
        "descricao": "Cria um novo cargo no servidor.",
        "uso": ">createrole <nome> [cor] [motivo]",
        "exemplos": [
            ">createrole Staff",
            ">createrole VIP #ff0000 Cargo de doador",
        ],
        "categoria": "role",
        "permissoes": "manage_roles",
    },
    "roleall": {
        "descricao": "Adiciona um cargo a TODOS os membros.",
        "uso": ">roleall @cargo",
        "exemplos": [">roleall @Membro"],
        "categoria": "role",
        "permissoes": "administrator",
        "observacoes": "⚠️ Ação em massa! Pode demorar",
    },
    "dehoist": {
        "descricao": "Adiciona prefixo em nicks que começam com "
                      "caractere especial (evita ficar no topo).",
        "uso": ">dehoist [prefixo]",
        "exemplos": [">dehoist", ">dehoist !"],
        "categoria": "role",
        "permissoes": "manage_nicknames",
    },

    # ─── Voz ───
    "moveall": {
        "descricao": "Move todos os membros de um canal de voz para "
                      "outro.",
        "uso": ">moveall <canal_origem> <canal_destino>",
        "exemplos": [">moveall Geral Lobby"],
        "categoria": "voice",
        "permissoes": "move_members",
    },
    "disconnect": {
        "descricao": "Desconecta um membro do canal de voz.",
        "uso": ">disconnect @membro",
        "exemplos": [">disconnect @Fulano"],
        "categoria": "voice",
        "permissoes": "move_members",
    },
    "voicekick": {
        "descricao": "Expulsa um membro do canal de voz.",
        "uso": ">voicekick @membro",
        "exemplos": [">voicekick @Fulano"],
        "categoria": "voice",
        "permissoes": "move_members",
    },
    "vcmute": {
        "descricao": "Muta um membro em canais de voz.",
        "uso": ">vcmute @membro",
        "exemplos": [">vcmute @Fulano"],
        "categoria": "voice",
        "permissoes": "mute_members",
    },

    # ─── Anti-nuke ───
    "antinuke": {
        "descricao": "Painel e configuração do sistema anti-nuke. "
                      "Protege contra raids, nukes, spam e ações "
                      "destrutivas em massa.",
        "uso": ">antinuke [feature] [state]",
        "exemplos": [
            ">antinuke",
            ">antinuke on",
            ">antinuke punishment ban",
            ">antinuke threshold 3",
        ],
        "categoria": "protection",
        "permissoes": "administrator",
        "observacoes": "Use `>antinuke` para ver todas as opções.",
    },
    "antiraid": {
        "descricao": "Sistema anti-raid. Detecta e bloqueia contas "
                      "novas, mass joins e comportamentos suspeitos.",
        "uso": ">antiraid [ação]",
        "exemplos": [
            ">antiraid",
            ">antiraid lockdown",
            ">antiraid panic",
        ],
        "categoria": "protection",
        "permissoes": "administrator",
    },
    "antiescalation": {
        "descricao": "Sistema anti-escalada de privilégios. Detecta "
                      "auto-promoção, delegação indevida, edição de "
                      "cargo elevando permissões e mais.",
        "uso": ">antiescalation [feature] [state]",
        "exemplos": [
            ">antiescalation",
            ">antiescalation on",
            ">antiescalation punishment strip",
        ],
        "categoria": "protection",
        "permissoes": "administrator",
        "aliases": ["ae", "antipromo"],
    },
    "protecao": {
        "descricao": "Desativa temporariamente a proteção contra "
                      "usuários (não afeta bots). Útil em manutenção.",
        "uso": ">protecao [off/on] <módulo>",
        "exemplos": [
            ">protecao",
            ">protecao off antinuke",
            ">protecao offall",
        ],
        "categoria": "protection",
        "permissoes": "administrator",
    },

    # ─── Logs ───
    "logs": {
        "descricao": "Sistema de logs customizáveis. Cada categoria "
                      "pode ter seu próprio canal.",
        "uso": ">logs [feature]",
        "exemplos": [
            ">logs",
            ">logs canal #logs",
            ">logs context mod #logs-mod",
        ],
        "categoria": "protection",
        "permissoes": "administrator",
    },
    "eventlogs": {
        "descricao": "Logs de eventos do servidor (join, leave, nick, "
                      "roles, mensagens editadas/deletadas, etc).",
        "uso": ">eventlogs [toggle] [key]",
        "exemplos": [
            ">eventlogs",
            ">eventlogs on",
            ">eventlogs toggle log_member_join",
        ],
        "categoria": "protection",
        "permissoes": "administrator",
    },

    # ─── Warn System ───
    "wsys": {
        "descricao": "Sistema de avisos com punições progressivas. "
                      "Detecta spam automaticamente e aplica punições "
                      "configuradas.",
        "uso": ">wsys [feature]",
        "exemplos": [
            ">wsys",
            ">wsys on",
            ">wsys addpunish 3 timeout 5",
            ">wsys confirm addrole @Staff",
        ],
        "categoria": "warn",
        "permissoes": "administrator",
    },
    "warn2": {
        "descricao": "Aplica um aviso em um usuário (registra a regra "
                      "quebrada).",
        "uso": ">warn2 @user <motivo> [auto]",
        "exemplos": [
            ">warn2 @Fulano Linguagem inadequada",
            ">warn2 @Fulano Spam auto",
        ],
        "categoria": "warn",
        "observacoes": "Use `auto` no final para aplicar sem "
                       "confirmação",
    },
    "warns2": {
        "descricao": "Lista os avisos de um usuário.",
        "uso": ">warns2 @user",
        "exemplos": [">warns2 @Fulano"],
        "categoria": "warn",
        "permissoes": "kick_members",
    },
    "warninfo": {
        "descricao": "Mostra detalhes de um aviso específico pelo ID.",
        "uso": ">warninfo <warn_id>",
        "exemplos": [">warninfo 507f1f77"],
        "categoria": "warn",
        "permissoes": "kick_members",
    },

    # ─── Permissões ───
    "perms": {
        "descricao": "Sistema de permissões por categoria. Controla "
                      "quem pode usar qual comando por cargo/user.",
        "uso": ">perms [subcomando]",
        "exemplos": [
            ">perms",
            ">perms category allow @Staff mod",
            ">perms command deny @Mod ban",
            ">perms user allow @Fulano ban",
            ">perms except add @Staff @Beltrano mod",
        ],
        "categoria": "config",
        "permissoes": "administrator",
    },

    # ─── Utilitários ───
    "embed": {
        "descricao": "Envia um embed customizado no canal.",
        "uso": ">embed <título> | <descrição>",
        "exemplos": [">embed Regras | Não use caps lock"],
        "categoria": "utility",
        "permissoes": "manage_messages",
    },
    "announce": {
        "descricao": "Envia um anúncio formatado.",
        "uso": ">announce <texto>",
        "exemplos": [">announce Servidor em manutenção às 22h"],
        "categoria": "utility",
        "permissoes": "manage_messages",
    },
    "poll": {
        "descricao": "Cria uma enquete com reações ✅/❌.",
        "uso": ">poll <pergunta>",
        "exemplos": [">poll Vocês preferem X ou Y?"],
        "categoria": "utility",
        "permissoes": "manage_messages",
    },
    "say": {
        "descricao": "Faz o bot enviar uma mensagem.",
        "uso": ">say <texto>",
        "exemplos": [">say Olá a todos!"],
        "categoria": "utility",
        "permissoes": "manage_messages",
    },

    # ─── Info ───
    "help": {
        "descricao": "Mostra a lista de comandos ou detalhes de um "
                      "comando específico.",
        "uso": ">help [comando]",
        "exemplos": [">help", ">help ban", ">help wsys"],
        "categoria": "info",
        "cooldown": "2s por usuário",
    },
    "ping": {
        "descricao": "Mostra a latência atual do bot.",
        "uso": ">ping",
        "exemplos": [">ping"],
        "categoria": "info",
    },
    "status": {
        "descricao": "Mostra o status geral do bot (latência, RAM, "
                      "guilds, cogs, comandos).",
        "uso": ">status",
        "exemplos": [">status"],
        "categoria": "info",
    },
    "userinfo": {
        "descricao": "Mostra informações de um usuário.",
        "uso": ">userinfo [@user]",
        "exemplos": [">userinfo", ">userinfo @Fulano"],
        "categoria": "info",
    },

    # ─── Sistema ───
    "health": {
        "descricao": "Health check completo do bot (RAM, latência, "
                      "MongoDB, cogs, uptime).",
        "uso": ">health",
        "exemplos": [">health"],
        "categoria": "info",
    },
    "mem": {
        "descricao": "Mostra uso detalhado de memória RAM.",
        "uso": ">mem",
        "exemplos": [">mem"],
        "categoria": "info",
    },
    "gc": {
        "descricao": "Força coleta de lixo (limpa RAM).",
        "uso": ">gc",
        "exemplos": [">gc"],
        "categoria": "info",
    },
    "opt": {
        "descricao": "Sistema de otimização. Mostra status do motor, "
                      "loop lag, tasks, rate limit e caches.",
        "uso": ">opt [subcomando]",
        "exemplos": [
            ">opt",
            ">opt stats",
            ">opt lag",
            ">opt cache",
        ],
        "categoria": "info",
        "permissoes": "owner",
    },
}


# ============================================================
# HELP CUSTOMIZADO
# ============================================================
# Substitui o help padrão do bot por esse.

class CommandsDescriptions(commands.Cog):
    """
    Substitui o help padrão por um help rico com descrições.
    """

    def __init__(self, bot):
        self.bot = bot

    # ============================================================
    # COMANDO: >help
    # ============================================================

    @commands.command(name="help")
    async def help_cmd(self, ctx, *, comando: str = None):
        """Mostra a lista de comandos ou detalhes de um."""
        # Com arg → detalhes do comando.
        if comando:
            return await self._help_comando(ctx, comando)
        # Sem arg → lista geral.
        return await self._help_geral(ctx)

    async def _help_comando(self, ctx, comando: str):
        """Mostra detalhes de UM comando."""
        # Normaliza: tira ">", deixa minúsculo.
        comando = comando.lower().lstrip(">")
        # Tenta achar o comando real no bot.
        cmd = self.bot.get_command(comando)

        # Pega a descrição do dicionário.
        info = DESCRICOES.get(comando)

        # ─── Se tem no dicionário, mostra rico. ───
        if info:
            embed = discord.Embed(
                title=f"📖 Comando: `>{comando}`",
                description=info.get("descricao", "*Sem descrição.*"),
                color=discord.Color.blue(),
                timestamp=_now())

            embed.add_field(
                name="📝 Uso",
                value=f"`{info.get('uso', '>?' + comando)}`",
                inline=False)

            # Exemplos.
            if info.get("exemplos"):
                embed.add_field(
                    name="💡 Exemplos",
                    value="\n".join(f"• `{e}`" for e in info["exemplos"]),
                    inline=False)

            # Aliases.
            if info.get("aliases"):
                embed.add_field(
                    name="🔀 Aliases",
                    value=", ".join(f"`{a}`" for a in info["aliases"]),
                    inline=True)

            # Categoria.
            if info.get("categoria"):
                embed.add_field(
                    name="📂 Categoria",
                    value=f"`{info['categoria']}`",
                    inline=True)

            # Permissões.
            if info.get("permissoes"):
                embed.add_field(
                    name="🔐 Permissão",
                    value=f"`{info['permissoes']}`",
                    inline=True)

            # Cooldown.
            if info.get("cooldown"):
                embed.add_field(
                    name="⏱️ Cooldown",
                    value=f"`{info['cooldown']}`",
                    inline=True)

            # Observações.
            if info.get("observacoes"):
                embed.add_field(
                    name="ℹ️ Observações",
                    value=info["observacoes"],
                    inline=False)

            embed.set_footer(
                text="Stumble Hit AntiRaid — Sistema de Ajuda")

            return await ctx.send(embed=embed)

        # ─── Se não tem no dicionário, mas o comando existe. ───
        if cmd:
            embed = discord.Embed(
                title=f"📖 Comando: `>{comando}`",
                description=cmd.help or "*Sem descrição disponível.*",
                color=discord.Color.blue(),
                timestamp=_now())
            embed.add_field(
                name="📝 Uso",
                value=f"`>{cmd.name} {cmd.signature}`",
                inline=False)
            if cmd.aliases:
                embed.add_field(
                    name="🔀 Aliases",
                    value=", ".join(f"`{a}`" for a in cmd.aliases),
                    inline=True)
            return await ctx.send(embed=embed)

        # ─── Não existe. ───
        return await ctx.send(embed=discord.Embed(
            title="❌ Erro",
            description=f"Comando `{comando}` não encontrado.",
            color=0xff0000))

    async def _help_geral(self, ctx):
        """Painel geral de ajuda."""
        embed = discord.Embed(
            title="Stumble Hit AntiRaid • Ajuda",
            description=(
                f"Use `>help <comando>` para detalhes de um comando "
                f"específico.\n"
                f"Exemplo: `>help ban`, `>help wsys`, `>help antinuke`"
            ),
            color=discord.Color.blue(),
            timestamp=_now())

        if ctx.guild and ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)

        # Agrupa comandos por categoria.
        por_categoria = {}
        for cmd_name, info in DESCRICOES.items():
            cat = info.get("categoria", "outros")
            por_categoria.setdefault(cat, []).append(cmd_name)

        # Ordem de exibição.
        ordem = [
            "mod", "purge", "message", "channel", "role", "voice",
            "protection", "warn", "config", "utility", "info",
        ]

        # Nomes bonitos pra cada categoria.
        nomes_bonitos = {
            "mod": "🛡️ Moderação",
            "purge": "🗑️ Purge",
            "message": "💬 Mensagens",
            "channel": "📁 Canais",
            "role": "👑 Cargos",
            "voice": "🎤 Voz",
            "protection": "🚨 Proteção",
            "warn": "⚠️ Avisos",
            "config": "⚙️ Config",
            "utility": "🔧 Utilitários",
            "info": "📊 Info",
            "outros": "📦 Outros",
        }

        # Adiciona um field por categoria.
        for cat in ordem + ["outros"]:
            if cat not in por_categoria:
                continue
            cmds = por_categoria[cat]
            # Lista comandos em formato `cmd`.
            lista = ", ".join(f"`{c}`" for c in cmds[:15])
            if len(cmds) > 15:
                lista += f" ... (+{len(cmds) - 15})"
            embed.add_field(
                name=nomes_bonitos.get(cat, cat),
                value=lista,
                inline=False)

        # Footer com contagem.
        total = len(list(self.bot.commands))
        embed.set_footer(
            text=f"Total de comandos do bot: {total} | "
                 f"Com descrição: {len(DESCRICOES)}")

        await ctx.send(embed=embed)


async def setup(bot):
    """Chamado pelo main.py."""
    if bot.get_cog("CommandsDescriptions") is None:
        cog = CommandsDescriptions(bot)
        await bot.add_cog(cog)
        # Remove o help padrão do ModAll se existir (evita duplicar).
        try:
            existing = bot.get_command("help")
            if existing and existing.cog and existing.cog.qualified_name == "ModAll":
                bot.remove_command("help")
        except Exception:
            pass