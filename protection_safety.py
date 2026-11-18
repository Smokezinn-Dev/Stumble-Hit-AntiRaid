"""Guardas compartilhadas para ações automáticas de moderação.

Toda decisão lê a configuração central diretamente do MongoDB para evitar
usar cache antigo após a staff desligar a proteção contra humanos ou alterar
a whitelist central. Em caso de falha de banco, a ação automática é bloqueada.
"""
from database import get_connection_async


async def fresh_global_config(guild_id: int) -> dict:
    """Retorna a configuração Anti-Nuke atual, sem cache.

    A leitura direta é intencional: esta função é uma barreira de segurança e
    não pode autorizar punições com base em uma configuração antiga em cache.
    """
    from commands_antinuke import DEFAULT_CONFIG as ANTINUKE_DEFAULTS
    # Obtém a conexão assíncrona sem bloquear o event loop do Discord.
    db = await get_connection_async()
    # Mantém a mesma coleção, filtro por servidor e defaults anteriores.
    doc = await db["antinuke_config"].find_one(
        {"guild_id": int(guild_id)}) or {}
    return {**ANTINUKE_DEFAULTS, **doc}


def is_globally_whitelisted(member, cfg: dict) -> bool:
    """Confere whitelist central de humanos, bots e cargos.

    Valores inválidos na lista fazem a checagem falhar fechada: não se aplica
    punição automática enquanto a lista não puder ser interpretada com
    segurança.
    """
    try:
        uid = int(member.id)
        users = {int(value) for value in (cfg.get("whitelist_users") or [])}
        bots = {int(value) for value in (cfg.get("whitelist_bots") or [])}
        roles = {int(value) for value in (cfg.get("whitelist_roles") or [])}
        role_exclusions = {int(value) for value in (
            cfg.get("whitelist_role_exclusions") or [])}
    except (TypeError, ValueError):
        return True
    # As listas são independentes: uma entrada humana nunca concede
    # imunidade a um bot, e uma entrada de bot nunca concede imunidade
    # a uma conta humana.
    if bool(getattr(member, "bot", False)):
        return uid in bots
    if uid in users:
        return True
    # Cargos são uma forma de whitelist humana; uma exceção individual
    # remove somente a imunidade herdada do cargo.
    if uid in role_exclusions:
        return False
    return any(int(role.id) in roles for role in getattr(member, "roles", []))


async def automatic_action_allowed(guild_id: int, member,
                                   require_human_protection: bool = True) -> bool:
    """Última validação central antes de uma ação automática.

    `False` significa não punir. Falha de banco também bloqueia a ação.
    """
    try:
        # Aguarda a configuração mais recente imediatamente antes da ação.
        cfg = await fresh_global_config(guild_id)
    except Exception:
        # Falha de banco continua bloqueando a ação automática (fail-closed).
        return False
    if (require_human_protection
            and not bool(getattr(member, "bot", False))
            and not bool(cfg.get("user_protection_enabled", True))):
        return False
    if is_globally_whitelisted(member, cfg):
        return False
    return True
