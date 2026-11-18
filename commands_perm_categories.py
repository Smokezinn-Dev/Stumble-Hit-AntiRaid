# ============================================================
# COMMANDS_PERM_CATEGORIES.PY - v1.0
# SISTEMA DE PERMISSÕES POR CATEGORIA
# ============================================================
# 📌 O QUE ESSE ARQUIVO FAZ?
# Controla QUEM pode usar QUAL comando. Diferente das permissões
# normais do Discord, aqui você pode:
#   • Dar permissão pra um cargo usar uma CATEGORIA de comandos
#   • Bloquear um comando específico pra um cargo
#   • Dar permissão direta pra um user
#   • Fazer exceções ("cargo X pode, exceto user Y")
#
# Ordem de prioridade (quando alguém tenta usar):
#   1. Owner → sempre pode
#   2. User allow/deny (regra direta) → decide
#   3. Exceção (cargo+user) → respeita
#   4. Cargo allow/deny categoria
#   5. Cargo allow/deny comando específico
#   6. Administrator → pode
#   7. Default → permite
# ============================================================

import discord
from discord.ext import commands
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from database import get_connection_async
from embeds import embed_success, embed_error, embed_warning, embed_info
from utils import TTLCache, GuildGate


def _now() -> datetime:
    """Hora atual em UTC."""
    return datetime.now(timezone.utc)


# ============================================================
# CATEGORIAS PADRÃO
# ============================================================
# Agrupa os comandos em categorias.

CATEGORIAS = {
    "mod": [
        "kick", "ban", "softban", "tempban", "unban", "massban",
        "timeout", "untimeout", "warn", "warns", "clearwarns",
        "strike", "unstrike", "silentban", "silentkick",
        "jail", "unjail", "strip", "restore",
    ],
    "purge": [
        "purge", "purgebots", "purgechannel", "nuke",
        "purgelink", "purgeimage", "purgeattach", "purgeregex",
        "purgeafter", "purgebefore", "purgemention", "purgeembed",
        "purgeinvite", "purgebot", "purgeuser", "purgepinguser",
        "purgenuke", "purgemulti", "purgeage", "purgestats",
    ],
    "channel": [
        "lock", "unlock", "lockdown", "unlockdown",
        "hide", "reveal", "freeze", "unfreeze",
        "hideall", "showall", "slowmode", "nsfw", "sfw",
        "clonechannel", "templock",
    ],
    "role": [
        "addrole", "removerole", "temprole", "createrole",
        "deleterole", "rolecolor", "roleinfo", "roleall",
        "roleunall", "dehoist", "rolepersist",
    ],
    "voice": [
        "moveall", "disconnect", "voicekick", "voiceban",
        "voiceunban", "cloneroom", "vcmute", "vcunmute",
    ],
    "message": [
        "snipe", "editsnipe",
    ],
    "utility": [
        "embed", "announce", "poll", "say",
    ],
    "info": [
        "userinfo", "serverinfo", "avatar", "botinfo", "roleinfo",
    ],
    "config": [
        "setconfig", "getconfig", "resetconfig",
        "setperm", "removeperm", "listperms",
    ],
    "staff": [
        "staff", "staffmetas", "staffadd", "staffranking",
        "staffhistory", "staffconfig", "staffmissionadd",
        "staffmissionremove", "staffgive", "staffremove",
        "staffauto", "staffreset",
    ],
    "protection": [
        "antinuke", "antiraid", "antiescalation", "protecao",
        "cconf", "confirmconfig", "pendingconfirms",
        "logs", "eventlogs",
    ],
    "warn": [
        "wsys", "warn2", "warns2", "warninfo", "unwarn",
        "clearwarns2",
    ],
    "backup": [
        "backup", "backupcreate", "backuplist", "backupinfo",
        "backupdelete", "backuprestore",
        "backuptext", "backuptextlimit", "backuptextlist",
    ],
    "control": [
        "control", "controlmodules", "controltoggle",
        "controlcommands", "controlcmd", "controlprefix",
        "controlmaintenance", "controlbypass", "controlstate",
    ],
    "automod": ["automod"],
    "cases": [
        "case", "cases", "note", "note2", "appeal",
    ],
    "verify": ["verification"],
}


# ============================================================
# MANAGER
# ============================================================

class PermManager:
    _cache = TTLCache(max_size=200, ttl=120)

    @classmethod
    async def get_config(cls, guild_id: int) -> dict:
        """Pega a configuração do cache ou, se necessário, do MongoDB via Motor."""
        cached = cls._cache.get(guild_id)
        if cached is not None:
            return cached
        # Obtém a conexão Motor; o await impede que a abertura/obtenção do banco bloqueie o event loop.
        db = await get_connection_async()
        # find_one é uma operação de rede assíncrona e precisa ser aguardada antes de montar a configuração.
        doc = await db["perm_categories_config"].find_one(
            {"guild_id": guild_id}) or {}
        cfg = {
            "enabled": doc.get("enabled", True),
            "default_policy": doc.get("default_policy", "allow"),
            # {role_id: {"categories": {"mod": "allow/deny"},
            #            "commands": {"kick": "allow/deny"}}}
            "role_rules": doc.get("role_rules", {}),
            # {user_id: {"kick": "allow/deny"}}
            "user_rules": doc.get("user_rules", {}),
            # {role_id: {"cat": [user_id_exceto, ...]}}
            "exceptions": doc.get("exceptions", {}),
            "notify_user": doc.get("notify_user", True),
            "log_channel": doc.get("log_channel", 0),
        }
        cls._cache.set(guild_id, cfg)
        return cfg

    @classmethod
    async def update(cls, guild_id: int, key: str, value):
        """Persiste uma configuração com Motor e invalida o cache após a gravação."""
        # Usa o mesmo banco assíncrono compartilhado pelo restante da aplicação.
        db = await get_connection_async()
        # Aguarda a confirmação da escrita para não anunciar sucesso antes da persistência.
        await db["perm_categories_config"].update_one(
            {"guild_id": guild_id}, {"$set": {key: value}}, upsert=True)
        # Invalida somente depois da gravação para evitar servir a configuração antiga.
        cls._cache.invalidate(guild_id)

    @classmethod
    def invalidate(cls, guild_id: int):
        cls._cache.invalidate(guild_id)


# ============================================================
# CHECK DE PERMISSÃO
# ============================================================

def get_categoria(cmd_name: str) -> Optional[str]:
    """Retorna a categoria de um comando (ou None)."""
    for cat, cmds in CATEGORIAS.items():
        if cmd_name in cmds:
            return cat
    return None


def pode_usar(cfg: dict, member: discord.Member,
               cmd_name: str) -> tuple:
    """
    Verifica se um membro pode usar um comando.
    Retorna (pode: bool, motivo: str).
    Motivo é o que decidiu (pra log).
    """
    # Sistema desligado? Permite.
    if not cfg.get("enabled"):
        return True, "disabled"

    # 1. Owner sempre pode.
    if member.id == member.guild.owner_id:
        return True, "owner"

    # Descobre a categoria do comando.
    cat = get_categoria(cmd_name)

    # 2. User rules (prioridade máxima).
    user_rules = cfg.get("user_rules", {}).get(str(member.id), {})
    if cmd_name in user_rules:
        return (user_rules[cmd_name] == "allow"), f"user_{user_rules[cmd_name]}"
    if cat and cat in user_rules:
        return (user_rules[cat] == "allow"), f"user_{user_rules[cat]}"

    # Se é admin, deixa passar (menos se tiver regra específica acima).
    is_admin = member.guild_permissions.administrator

    # 3. Cargo rules.
    role_rules = cfg.get("role_rules", {})
    exceptions = cfg.get("exceptions", {})

    # Flags que vamos preencher.
    allow_cat = False
    deny_cat = False
    allow_cmd = False
    deny_cmd = False

    # Percorre os cargos do membro.
    for role in member.roles:
        rid = str(role.id)
        rules = role_rules.get(rid, {})
        cats = rules.get("categories", {})
        cmds = rules.get("commands", {})

        # Exceção: esse cargo tem exceção pra esse user nessa categoria?
        role_exc = exceptions.get(rid, {})
        if cat and cat in role_exc:
            if member.id in role_exc[cat]:
                # User está na exceção desse cargo → pula esse cargo.
                continue

        # Marca allow/deny de categoria.
        if cat:
            if cats.get(cat) == "allow":
                allow_cat = True
            elif cats.get(cat) == "deny":
                deny_cat = True

        # Marca allow/deny de comando.
        if cmd_name in cmds:
            if cmds[cmd_name] == "allow":
                allow_cmd = True
            elif cmds[cmd_name] == "deny":
                deny_cmd = True

    # Prioridade: comando específico > categoria.
    if deny_cmd:
        # Admin pode ter bypass (se config ligado).
        if is_admin and cfg.get("admin_bypass", False):
            return True, "admin"
        return False, "cargo_deny_cmd"
    if allow_cmd:
        return True, "cargo_allow_cmd"
    if deny_cat:
        if is_admin and cfg.get("admin_bypass", False):
            return True, "admin"
        return False, "cargo_deny_cat"
    if allow_cat:
        return True, "cargo_allow_cat"

    # 4. Admin.
    if is_admin:
        return True, "admin"

    # 5. Default.
    default = cfg.get("default_policy", "allow")
    return (default == "allow"), f"default_{default}"


# ============================================================
# COG
# ============================================================

class PermCategoriesCommands(commands.Cog):
    """
    Sistema de permissões por categoria.
    Controla quem pode usar qual comando (por cargo/user/exceção).
    """

    def __init__(self, bot):
        self.bot = bot

    # ============================================================
    # CHECK GLOBAL (roda antes de todo comando)
    # ============================================================

    @commands.Cog.listener()
    async def on_command(self, ctx):
        """Verifica permissão antes de cada comando."""
        # Só age em guilds com comando.
        if not ctx.guild or not ctx.command:
            return

        # Ignora o dono do bot.
        if ctx.author.id == self.bot.owner_id:
            return

        # O check global consulta a configuração atual antes de autorizar qualquer comando.
        cfg = await PermManager.get_config(ctx.guild.id)
        if not cfg.get("enabled"):
            return

        # Pega o nome do comando (sem subcomando).
        cmd_name = ctx.command.qualified_name.split()[0]
        pode, motivo = pode_usar(cfg, ctx.author, cmd_name)

        if not pode:
            # Reage com 🚫.
            try:
                await ctx.message.add_reaction("🚫")
            except Exception:
                pass

            # Avisa o user (se configurado).
            if cfg.get("notify_user"):
                try:
                    await ctx.send(embed=embed_error(
                        f"❌ Você não tem permissão para usar `{cmd_name}`."),
                        delete_after=6)
                except Exception:
                    pass

            # Loga a negação.
            await self._logar_negado(ctx, cmd_name, motivo)

            # Cancela o comando (levanta exceção).
            raise commands.CheckFailure(
                f"Sem permissão para {cmd_name}")

    async def _logar_negado(self, ctx, cmd_name: str, motivo: str):
        """Envia log de comando negado (se tiver canal configurado)."""
        try:
            cfg = await PermManager.get_config(ctx.guild.id)
            log_id = cfg.get("log_channel")
            if not log_id:
                return
            canal = ctx.guild.get_channel(int(log_id))
            if not canal:
                return

            # Log profissional.
            from embeds_professional import ProfessionalLog
            log = ProfessionalLog(
                title="Comando Bloqueado",
                description=(
                    "Um usuário tentou usar um comando sem permissão."
                ),
                severity="low",
                footer="Stumble Hit AntiRaid • Permissões")
            log.set_target(ctx.author)
            log.add_field("Comando", f"`{cmd_name}`")
            log.add_field("Canal", ctx.channel.mention)
            log.add_field("Motivo", f"`{motivo}`")
            await canal.send(embed=log.build())
        except Exception:
            pass

    # ============================================================
    # COMANDO PRINCIPAL: >perms
    # ============================================================

    @commands.group(name="perms", aliases=["permissoes", "perm"],
                    invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def perms(self, ctx):
        """Painel do sistema de permissões por categoria."""
        cfg = await PermManager.get_config(ctx.guild.id)

        def onoff(v):
            return "✅" if v else "❌"

        embed = discord.Embed(
            title="🔐 Permissões por Categoria",
            description=(
                f"**Status:** {onoff(cfg['enabled'])}\n"
                f"**Política padrão:** `{cfg['default_policy']}`\n"
                f"**Notificar user:** {onoff(cfg['notify_user'])}\n"
                f"**Log:** "
                f"{'<#' + str(cfg['log_channel']) + '>' if cfg.get('log_channel') else '❌'}"
            ),
            color=discord.Color.purple(),
            timestamp=_now())

        # Lista as categorias.
        cats_lines = []
        for cat, cmds in CATEGORIAS.items():
            cats_lines.append(f"• `{cat}` ({len(cmds)})")
        embed.add_field(
            name=f"📂 Categorias ({len(CATEGORIAS)})",
            value="\n".join(cats_lines[:17]),
            inline=True)

        # Comandos disponíveis.
        embed.add_field(
            name="📌 Comandos",
            value=(
                "`>perms category allow/deny @cargo <cat>`\n"
                "`>perms command allow/deny @cargo <cmd>`\n"
                "`>perms user allow/deny @user <cmd>`\n"
                "`>perms except add/remove @cargo @user <cat>`\n"
                "`>perms list`\n"
                "`>perms check @user <cmd>`\n"
                "`>perms categories`\n"
                "`>perms reset`"
            ),
            inline=False)

        await ctx.send(embed=embed)

    @perms.command(name="on")
    @commands.has_permissions(administrator=True)
    async def perms_on(self, ctx):
        """Liga o sistema."""
        await PermManager.update(ctx.guild.id, "enabled", True)
        await ctx.send(embed=embed_success("✅ Sistema ativado."))

    @perms.command(name="off")
    @commands.has_permissions(administrator=True)
    async def perms_off(self, ctx):
        """Desliga o sistema."""
        await PermManager.update(ctx.guild.id, "enabled", False)
        await ctx.send(embed=embed_success("✅ Sistema desativado."))

    @perms.command(name="categories", aliases=["categorias"])
    @commands.has_permissions(administrator=True)
    async def perms_categories(self, ctx):
        """Lista todas as categorias e seus comandos."""
        embed = discord.Embed(
            title="📂 Categorias de Comandos",
            color=discord.Color.purple(),
            timestamp=_now())
        for cat, cmds in CATEGORIAS.items():
            embed.add_field(
                name=f"`{cat}`",
                value=", ".join(f"`{c}`" for c in cmds[:20]),
                inline=False)
        await ctx.send(embed=embed)

    # ============================================================
    # CATEGORY (por categoria)
    # ============================================================

    @perms.group(name="category", aliases=["categoria"],
                  invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def perms_category(self, ctx):
        """Grupo pra configurar permissões por categoria."""
        await ctx.send(embed=embed_info(
            "Use: `>perms category allow/deny @cargo <categoria>`"))

    @perms_category.command(name="allow", aliases=["permitir"])
    @commands.has_permissions(administrator=True)
    async def perms_cat_allow(self, ctx, role: discord.Role, categoria: str):
        """Permite que um cargo use uma categoria."""
        if categoria not in CATEGORIAS:
            return await ctx.send(embed=embed_error(
                f"❌ Categoria `{categoria}` não existe.\n"
                f"Use `>perms categories` para ver."))

        cfg = await PermManager.get_config(ctx.guild.id)
        rules = dict(cfg.get("role_rules", {}))
        rid = str(role.id)
        role_data = dict(rules.get(rid, {}))
        cats = dict(role_data.get("categories", {}))
        cats[categoria] = "allow"
        role_data["categories"] = cats
        rules[rid] = role_data
        await PermManager.update(ctx.guild.id, "role_rules", rules)

        await ctx.send(embed=embed_success(
            f"✅ {role.mention} **pode** usar a categoria `{categoria}`."))

    @perms_category.command(name="deny", aliases=["negar"])
    @commands.has_permissions(administrator=True)
    async def perms_cat_deny(self, ctx, role: discord.Role, categoria: str):
        """Bloqueia uma categoria pra um cargo."""
        if categoria not in CATEGORIAS:
            return await ctx.send(embed=embed_error(
                f"❌ Categoria `{categoria}` não existe."))

        cfg = await PermManager.get_config(ctx.guild.id)
        rules = dict(cfg.get("role_rules", {}))
        rid = str(role.id)
        role_data = dict(rules.get(rid, {}))
        cats = dict(role_data.get("categories", {}))
        cats[categoria] = "deny"
        role_data["categories"] = cats
        rules[rid] = role_data
        await PermManager.update(ctx.guild.id, "role_rules", rules)

        await ctx.send(embed=embed_success(
            f"✅ {role.mention} **NÃO pode** usar a categoria "
            f"`{categoria}`."))

    @perms_category.command(name="clear", aliases=["limpar"])
    @commands.has_permissions(administrator=True)
    async def perms_cat_clear(self, ctx, role: discord.Role, categoria: str):
        """Remove a regra de uma categoria."""
        cfg = await PermManager.get_config(ctx.guild.id)
        rules = dict(cfg.get("role_rules", {}))
        rid = str(role.id)
        role_data = dict(rules.get(rid, {}))
        cats = dict(role_data.get("categories", {}))
        cats.pop(categoria, None)
        role_data["categories"] = cats
        rules[rid] = role_data
        await PermManager.update(ctx.guild.id, "role_rules", rules)
        await ctx.send(embed=embed_success(
            f"✅ Regra de `{categoria}` removida para {role.mention}."))

    # ============================================================
    # COMMAND (por comando)
    # ============================================================

    @perms.group(name="command", aliases=["comando"],
                  invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def perms_command(self, ctx):
        """Grupo pra configurar permissões por comando."""
        await ctx.send(embed=embed_info(
            "Use: `>perms command allow/deny @cargo <comando>`"))

    @perms_command.command(name="allow", aliases=["permitir"])
    @commands.has_permissions(administrator=True)
    async def perms_cmd_allow(self, ctx, role: discord.Role,
                                comando: str):
        """Permite um comando específico pra um cargo."""
        comando = comando.lower().lstrip(">")
        cfg = await PermManager.get_config(ctx.guild.id)
        rules = dict(cfg.get("role_rules", {}))
        rid = str(role.id)
        role_data = dict(rules.get(rid, {}))
        cmds = dict(role_data.get("commands", {}))
        cmds[comando] = "allow"
        role_data["commands"] = cmds
        rules[rid] = role_data
        await PermManager.update(ctx.guild.id, "role_rules", rules)

        await ctx.send(embed=embed_success(
            f"✅ {role.mention} **pode** usar `>{comando}`."))

    @perms_command.command(name="deny", aliases=["negar"])
    @commands.has_permissions(administrator=True)
    async def perms_cmd_deny(self, ctx, role: discord.Role,
                               comando: str):
        """Bloqueia um comando específico pra um cargo."""
        comando = comando.lower().lstrip(">")
        cfg = await PermManager.get_config(ctx.guild.id)
        rules = dict(cfg.get("role_rules", {}))
        rid = str(role.id)
        role_data = dict(rules.get(rid, {}))
        cmds = dict(role_data.get("commands", {}))
        cmds[comando] = "deny"
        role_data["commands"] = cmds
        rules[rid] = role_data
        await PermManager.update(ctx.guild.id, "role_rules", rules)

        await ctx.send(embed=embed_success(
            f"✅ {role.mention} **NÃO pode** usar `>{comando}`."))

    @perms_command.command(name="clear", aliases=["limpar"])
    @commands.has_permissions(administrator=True)
    async def perms_cmd_clear(self, ctx, role: discord.Role,
                                comando: str):
        """Remove a regra de um comando."""
        comando = comando.lower().lstrip(">")
        cfg = await PermManager.get_config(ctx.guild.id)
        rules = dict(cfg.get("role_rules", {}))
        rid = str(role.id)
        role_data = dict(rules.get(rid, {}))
        cmds = dict(role_data.get("commands", {}))
        cmds.pop(comando, None)
        role_data["commands"] = cmds
        rules[rid] = role_data
        await PermManager.update(ctx.guild.id, "role_rules", rules)
        await ctx.send(embed=embed_success(
            f"✅ Regra de `>{comando}` removida para {role.mention}."))

    # ============================================================
    # USER (por usuário)
    # ============================================================

    @perms.group(name="user", aliases=["usuario"],
                  invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def perms_user(self, ctx):
        """Grupo pra configurar permissões por user."""
        await ctx.send(embed=embed_info(
            "Use: `>perms user allow/deny @user <comando>`"))

    @perms_user.command(name="allow", aliases=["permitir"])
    @commands.has_permissions(administrator=True)
    async def perms_user_allow(self, ctx, user: discord.Member,
                                 comando: str):
        """Permite um comando pra um user específico."""
        comando = comando.lower().lstrip(">")
        cfg = await PermManager.get_config(ctx.guild.id)
        rules = dict(cfg.get("user_rules", {}))
        uid = str(user.id)
        user_data = dict(rules.get(uid, {}))
        user_data[comando] = "allow"
        rules[uid] = user_data
        await PermManager.update(ctx.guild.id, "user_rules", rules)

        await ctx.send(embed=embed_success(
            f"✅ {user.mention} **pode** usar `>{comando}`."))

    @perms_user.command(name="deny", aliases=["negar"])
    @commands.has_permissions(administrator=True)
    async def perms_user_deny(self, ctx, user: discord.Member,
                                comando: str):
        """Bloqueia um comando pra um user."""
        comando = comando.lower().lstrip(">")
        cfg = await PermManager.get_config(ctx.guild.id)
        rules = dict(cfg.get("user_rules", {}))
        uid = str(user.id)
        user_data = dict(rules.get(uid, {}))
        user_data[comando] = "deny"
        rules[uid] = user_data
        await PermManager.update(ctx.guild.id, "user_rules", rules)

        await ctx.send(embed=embed_success(
            f"✅ {user.mention} **NÃO pode** usar `>{comando}`."))

    @perms_user.command(name="clear", aliases=["limpar"])
    @commands.has_permissions(administrator=True)
    async def perms_user_clear(self, ctx, user: discord.Member,
                                 comando: str):
        """Remove a regra de um user."""
        comando = comando.lower().lstrip(">")
        cfg = await PermManager.get_config(ctx.guild.id)
        rules = dict(cfg.get("user_rules", {}))
        uid = str(user.id)
        user_data = dict(rules.get(uid, {}))
        user_data.pop(comando, None)
        rules[uid] = user_data
        await PermManager.update(ctx.guild.id, "user_rules", rules)
        await ctx.send(embed=embed_success(
            f"✅ Regra de `>{comando}` removida para {user.mention}."))

    # ============================================================
    # EXCEÇÕES
    # ============================================================

    @perms.group(name="except", aliases=["excecao", "exceto"],
                  invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def perms_except(self, ctx):
        """Grupo pra configurar exceções."""
        await ctx.send(embed=embed_info(
            "Use: `>perms except add/remove @cargo @user <categoria>`"))

    @perms_except.command(name="add", aliases=["adicionar"])
    @commands.has_permissions(administrator=True)
    async def perms_except_add(self, ctx, role: discord.Role,
                                 user: discord.Member, categoria: str):
        """
        Exceção: cargo X pode usar categoria, EXCETO user Y.
        """
        if categoria not in CATEGORIAS:
            return await ctx.send(embed=embed_error(
                f"❌ Categoria `{categoria}` não existe."))

        cfg = await PermManager.get_config(ctx.guild.id)
        exceptions = dict(cfg.get("exceptions", {}))
        rid = str(role.id)
        role_exc = dict(exceptions.get(rid, {}))
        cat_list = list(role_exc.get(categoria, []))
        if user.id not in cat_list:
            cat_list.append(user.id)
        role_exc[categoria] = cat_list
        exceptions[rid] = role_exc
        await PermManager.update(ctx.guild.id, "exceptions", exceptions)

        await ctx.send(embed=embed_success(
            f"✅ {role.mention} pode usar `{categoria}`, "
            f"**EXCETO** {user.mention}."))

    @perms_except.command(name="remove", aliases=["remover"])
    @commands.has_permissions(administrator=True)
    async def perms_except_remove(self, ctx, role: discord.Role,
                                    user: discord.Member,
                                    categoria: str):
        """Remove uma exceção."""
        cfg = await PermManager.get_config(ctx.guild.id)
        exceptions = dict(cfg.get("exceptions", {}))
        rid = str(role.id)
        role_exc = dict(exceptions.get(rid, {}))
        cat_list = list(role_exc.get(categoria, []))
        if user.id in cat_list:
            cat_list.remove(user.id)
        role_exc[categoria] = cat_list
        exceptions[rid] = role_exc
        await PermManager.update(ctx.guild.id, "exceptions", exceptions)

        await ctx.send(embed=embed_success(
            f"✅ Exceção removida."))

    # ============================================================
    # LIST / CHECK / RESET
    # ============================================================

    @perms.command(name="list", aliases=["lista"])
    @commands.has_permissions(administrator=True)
    async def perms_list(self, ctx):
        """Lista TODAS as regras configuradas."""
        cfg = await PermManager.get_config(ctx.guild.id)

        embed = discord.Embed(
            title="📋 Regras de Permissão",
            color=discord.Color.purple(),
            timestamp=_now())

        # Cargos.
        role_rules = cfg.get("role_rules", {})
        if role_rules:
            linhas = []
            for rid, rules in list(role_rules.items())[:10]:
                role = ctx.guild.get_role(int(rid))
                if not role:
                    continue
                cats = rules.get("categories", {})
                cmds = rules.get("commands", {})
                resumo = []
                for c, v in cats.items():
                    resumo.append(f"{c}:{v}")
                for c, v in cmds.items():
                    resumo.append(f">{c}:{v}")
                linhas.append(
                    f"**{role.mention}**: " +
                    ", ".join(f"`{r}`" for r in resumo[:6]))
            if linhas:
                embed.add_field(
                    name=f"👑 Cargos ({len(role_rules)})",
                    value="\n".join(linhas),
                    inline=False)

        # Users.
        user_rules = cfg.get("user_rules", {})
        if user_rules:
            linhas = []
            for uid, rules in list(user_rules.items())[:10]:
                user = ctx.guild.get_member(int(uid))
                if not user:
                    continue
                resumo = [f">{c}:{v}" for c, v in rules.items()]
                linhas.append(
                    f"**{user.mention}**: " +
                    ", ".join(f"`{r}`" for r in resumo[:6]))
            if linhas:
                embed.add_field(
                    name=f"👤 Users ({len(user_rules)})",
                    value="\n".join(linhas),
                    inline=False)

        # Exceções.
        exceptions = cfg.get("exceptions", {})
        if exceptions:
            linhas = []
            for rid, role_exc in list(exceptions.items())[:5]:
                role = ctx.guild.get_role(int(rid))
                if not role:
                    continue
                for cat, users in role_exc.items():
                    nomes = [f"<@{u}>" for u in users[:3]]
                    linhas.append(
                        f"**{role.mention}** exceto em `{cat}`: " +
                        ", ".join(nomes))
            if linhas:
                embed.add_field(
                    name=f"⚡ Exceções ({len(exceptions)})",
                    value="\n".join(linhas),
                    inline=False)

        if not (role_rules or user_rules or exceptions):
            embed.description = "*Nenhuma regra configurada.*"

        await ctx.send(embed=embed)

    @perms.command(name="check", aliases=["verificar"])
    @commands.has_permissions(administrator=True)
    async def perms_check(self, ctx, user: discord.Member, comando: str):
        """Verifica se um user pode usar um comando."""
        comando = comando.lower().lstrip(">")
        cfg = await PermManager.get_config(ctx.guild.id)
        pode, motivo = pode_usar(cfg, user, comando)

        cor = discord.Color.green() if pode else discord.Color.red()
        emoji = "✅" if pode else "❌"

        embed = discord.Embed(
            title=f"{emoji} Resultado da Verificação",
            color=cor,
            timestamp=_now())
        embed.add_field(name="Usuário", value=user.mention, inline=True)
        embed.add_field(name="Comando", value=f"`>{comando}`", inline=True)
        embed.add_field(name="Pode usar?",
                        value=f"**{'SIM' if pode else 'NÃO'}**",
                        inline=True)
        embed.add_field(name="Motivo", value=f"`{motivo}`", inline=False)

        await ctx.send(embed=embed)

    @perms.command(name="default", aliases=["padrao"])
    @commands.has_permissions(administrator=True)
    async def perms_default(self, ctx, policy: str):
        """Define a política padrão (allow/deny)."""
        if policy.lower() not in ("allow", "deny"):
            return await ctx.send(embed=embed_error(
                "❌ Use: `allow` ou `deny`"))
        await PermManager.update(ctx.guild.id, "default_policy", policy.lower())
        await ctx.send(embed=embed_success(
            f"✅ Política padrão: `{policy.lower()}`"))

    @perms.command(name="notify")
    @commands.has_permissions(administrator=True)
    async def perms_notify(self, ctx, state: str):
        """Liga/desliga notificar o user quando negado."""
        enabled = state.lower() in ("on", "true", "1", "sim")
        await PermManager.update(ctx.guild.id, "notify_user", enabled)
        await ctx.send(embed=embed_success(
            f"✅ Notificar user: {'ON' if enabled else 'OFF'}"))

    @perms.command(name="log")
    @commands.has_permissions(administrator=True)
    async def perms_log(self, ctx, canal: discord.TextChannel):
        """Define canal de log de negações."""
        await PermManager.update(ctx.guild.id, "log_channel", canal.id)
        await ctx.send(embed=embed_success(f"✅ Log em {canal.mention}"))

    @perms.command(name="reset")
    @commands.has_permissions(administrator=True)
    async def perms_reset(self, ctx):
        """Reseta TODAS as regras."""
        # Obtém a conexão Motor para que a exclusão também seja não bloqueante.
        db = await get_connection_async()
        # Aguarda a remoção antes de limpar o cache e confirmar o reset ao administrador.
        await db["perm_categories_config"].delete_one(
            {"guild_id": ctx.guild.id})
        # Evita que a próxima verificação reutilize regras que acabaram de ser apagadas.
        PermManager.invalidate(ctx.guild.id)
        await ctx.send(embed=embed_warning(
            "⚠️ Todas as regras foram resetadas."))


async def setup(bot):
    """Chamado pelo main.py."""
    if bot.get_cog("PermCategoriesCommands") is None:
        await bot.add_cog(PermCategoriesCommands(bot))