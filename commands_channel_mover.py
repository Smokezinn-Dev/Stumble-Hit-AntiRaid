# ============================================================
# COMMANDS_CHANNEL_MOVER.PY - v1.1
# MOVER CATEGORIAS E CANAIS POR COMANDO
# ============================================================
#
# 📌 O QUE ESSE ARQUIVO FAZ:
# - Move categorias e canais pra cima/baixo/topo/fundo
# - Move canal pra dentro de outra categoria
# - Mostra a ordem atual com índices (pra você saber o que mover)
#
# 📌 POR QUE EXISTE:
# - Porque arrastar com o dedo no celular é ruim
# - Comandos funcionam de qualquer device, sem precisar arrastar
#
# 📌 v1.1 (CORREÇÃO):
# - Categorias NÃO podem ser mencionadas com @ (só cargos/membros)
# - Agora aceita: `#nome-da-categoria`, ID puro ou nome sem #
# - Adicionado helper _resolver_categoria() que aceita 3 formatos
#
# 📌 COMO USAR:
#   >mover categoria #nome cima         → sobe 1 posição
#   >mover categoria 123456789 cima     → ID puro
#   >mover categoria "Nome Legal" top   → nome com espaços
#   >mover categoria #nome bottom       → vai pro fundo
#   >mover categoria #nome 3            → vai pra posição exata 3
#
#   >mover canal #canal cima            → sobe 1 posição
#   >mover canal #canal baixo           → desce 1 posição
#   >mover canal #canal top             → topo da categoria atual
#   >mover canal #canal bottom          → fundo da categoria atual
#   >mover canal #canal 2               → posição exata 2
#   >mover canal #canal #nova-cat       → move pra outra categoria
#
#   >mover listar                       → mostra tudo com índices
#
# ============================================================

# ─── BLOCO 1 — IMPORTS ───

import asyncio      # pra rodar coisas em paralelo
import re           # pra usar regex (buscar padrões em texto)
from typing import Optional, List, Union

import discord
from discord.ext import commands

# Embeds coloridos prontos.
from embeds import embed_success, embed_error, embed_warning, embed_info


# ─── BLOCO 2 — TEXTOS CONFIGURÁVEIS ───
# Mude aqui os textos que aparecem pro usuário.

TXT_SEM_PERMISSAO = "❌ Você precisa da permissão **Gerenciar Canais** pra usar isso."
TXT_CATEGORIA_NAO_ACHADA = "❌ Categoria não encontrada. Use `#nome`, ID ou nome exato."
TXT_CANAL_NAO_ACHADO = "❌ Canal não encontrado."
TXT_JA_NO_TOPO = "⚠️ Já está no topo."
TXT_JA_NO_FUNDO = "⚠️ Já está no fundo."
TXT_MOVIDO = "✅ Movido com sucesso!"
TXT_ORDEM_ATUAL = "📋 Ordem atual"
TXT_FOOTER = "Stumble Hit AntiRaid • Mover Canais"
TXT_AMBIGUO = "⚠️ Achei mais de uma categoria com esse nome. Use o ID."


# ─── BLOCO 3 — HELPERS (funções auxiliares) ───

def _categorias_ordenadas(guild: discord.Guild) -> List[discord.CategoryChannel]:
    """Retorna as categorias da guild ordenadas por position."""
    return sorted(guild.categories, key=lambda c: c.position)


def _canais_da_categoria(category: Optional[discord.CategoryChannel],
                          guild: discord.Guild) -> List[discord.abc.GuildChannel]:
    """Retorna canais de uma categoria (ou sem categoria) ordenados por position."""
    if category is None:
        # Sem categoria = canais que não estão em nenhuma categoria.
        canais = [c for c in guild.channels if c.category is None]
    else:
        # Pega os canais da categoria.
        canais = list(category.channels)
    # Remove categorias da lista (não queremos mover categoria dentro de categoria).
    canais = [c for c in canais if not isinstance(c, discord.CategoryChannel)]
    return sorted(canais, key=lambda c: c.position)


def _resolver_categoria(guild: discord.Guild, valor: str
                         ) -> Union[discord.CategoryChannel, str]:
    """
    Resolve uma string em uma CategoryChannel.

    Aceita TRÊS formatos:
      1) Menção de canal <#123456> (o Discord usa pra categoria também)
      2) ID puro (123456789012345678)
      3) Nome exato entre aspas ou sem (busca case-insensitive)

    Retorna:
      - A CategoryChannel, se achou 1
      - String de erro, se não achou ou se achou várias (ambíguo)
    """
    if not valor:
        return TXT_CATEGORIA_NAO_ACHADA

    valor = valor.strip()

    # Remove aspas se tiver ("Nome Legal" ou 'Nome Legal')
    if (valor.startswith('"') and valor.endswith('"')) or \
       (valor.startswith("'") and valor.endswith("'")):
        valor = valor[1:-1].strip()

    # ─── CASO 1: Menção <#123> ───
    m = re.fullmatch(r"<#(\d+)>", valor)
    if m:
        cat_id = int(m.group(1))
        cat = guild.get_channel(cat_id)
        if isinstance(cat, discord.CategoryChannel):
            return cat
        # Pode ter mencionado um canal comum — tenta achar a categoria dele.
        if cat and cat.category:
            return cat.category
        return TXT_CATEGORIA_NAO_ACHADA

    # ─── CASO 2: ID puro ───
    if re.fullmatch(r"\d{15,25}", valor):
        cat_id = int(valor)
        cat = guild.get_channel(cat_id)
        if isinstance(cat, discord.CategoryChannel):
            return cat
        return TXT_CATEGORIA_NAO_ACHADA

    # ─── CASO 3: Nome exato (case-insensitive) ───
    alvo = valor.lower()
    encontradas = [c for c in guild.categories if c.name.lower() == alvo]

    if len(encontradas) == 1:
        return encontradas[0]

    # Se achou várias, é ambíguo.
    if len(encontradas) > 1:
        ids = ", ".join(f"`{c.id}`" for c in encontradas[:5])
        return f"{TXT_AMBIGUO}\nIDs: {ids}"

    return TXT_CATEGORIA_NAO_ACHADA


async def _aplicar_posicoes(guild: discord.Guild,
                             itens: List[discord.abc.GuildChannel]):
    """
    Aplica posições em lote — mais rápido e evita rate limit.
    Cada item da lista vai receber position = índice (0, 1, 2...).
    """
    # Cria dicionário {item: nova_posição}.
    positions = {item: idx for idx, item in enumerate(itens)}
    try:
        # API do Discord aceita batch (vários de uma vez).
        await guild.edit_channel_positions(positions=positions)
    except Exception:
        # Fallback: edita um por um.
        for item in itens:
            try:
                await item.edit(position=positions[item])
            except Exception:
                pass


async def _aplicar_posicoes_categorias(guild: discord.Guild,
                                         categorias: List[discord.CategoryChannel]):
    """Aplica posições nas categorias."""
    positions = {cat: idx for idx, cat in enumerate(categorias)}
    try:
        await guild.edit_channel_positions(positions=positions)
    except Exception:
        # Fallback um por um.
        for idx, cat in enumerate(categorias):
            if cat.position != idx:
                try:
                    await cat.edit(position=idx)
                except Exception:
                    pass


# ─── BLOCO 4 — COG PRINCIPAL ───

class ChannelMoverCommands(commands.Cog):
    """
    Cog que implementa o sistema de mover categorias e canais.
    PERMISSÕES: só quem tem `manage_channels` (Gerenciar Canais).
    """

    def __init__(self, bot):
        self.bot = bot

    # ═══════════════════════════════════════════════════════════
    # COMANDO PRINCIPAL: >mover
    # ═══════════════════════════════════════════════════════════

    @commands.group(name="mover", aliases=["move", "mv"],
                     invoke_without_command=True)
    @commands.has_permissions(manage_channels=True)
    async def mover(self, ctx):
        """Comando principal. Sem args = mostra ajuda."""
        embed = discord.Embed(
            title="📦 Mover Categorias e Canais",
            description=(
                "Sistema pra mover categorias/canais sem precisar "
                "arrastar com o dedo (útil no celular com touch bugado)."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="📁 Mover Categoria",
            value=(
                "⚠️ Categorias **NÃO** são mencionáveis com `@`.\n"
                "Use `#nome`, ID ou nome entre aspas.\n\n"
                "`>mover categoria #nome cima`\n"
                "`>mover categoria 123456789 cima`\n"
                "`>mover categoria \"Nome Legal\" top`\n"
                "`>mover categoria #nome bottom`\n"
                "`>mover categoria #nome 3`"
            ),
            inline=False,
        )
        embed.add_field(
            name="💬 Mover Canal",
            value=(
                "`>mover canal #canal cima`\n"
                "`>mover canal #canal baixo`\n"
                "`>mover canal #canal top`\n"
                "`>mover canal #canal bottom`\n"
                "`>mover canal #canal 2`\n"
                "`>mover canal #canal #nova-cat`"
            ),
            inline=False,
        )
        embed.add_field(
            name="📋 Ver ordem atual",
            value="`>mover listar`",
            inline=False,
        )
        embed.set_footer(text=TXT_FOOTER)
        await ctx.send(embed=embed)

    # ═══════════════════════════════════════════════════════════
    # SUBCOMANDO: >mover categoria
    # ═══════════════════════════════════════════════════════════
    # NOTA: usamos `*, argumentos` (nargs) porque o nome pode ter
    # espaços. Então o usuário passa: >mover categoria #Nome Legal cima
    # e nós parseamos o final (cima/baixo/top/bottom/número) como ação.

    @mover.command(name="categoria", aliases=["cat", "category"])
    @commands.has_permissions(manage_channels=True)
    async def mover_categoria(self, ctx, *, argumentos: str):
        """
        Move uma categoria.

        Formatos aceitos:
          >mover categoria #nome cima
          >mover categoria "Nome Legal" top
          >mover categoria 123456789 bottom
          >mover categoria #nome 3
        """
        if not argumentos:
            return await ctx.send(embed=embed_error(
                "Use: `>mover categoria #nome cima/baixo/top/bottom/3`"
            ))

        # ─── Parse: últimas palavra = ação, resto = nome/id ───
        argumentos = argumentos.strip()

        # Regex: pega tudo até o último espaço (alvo) + última palavra (ação).
        m = re.match(r"^(.*?)\s+(\S+)$", argumentos)
        if not m:
            return await ctx.send(embed=embed_error(
                "Formato inválido. Use: `>mover categoria <nome> <ação>`"
            ))

        alvo_str = m.group(1).strip()  # grupo 1 = alvo
        acao = m.group(2).strip().lower()  # grupo 2 = ação

        # Remove aspas do alvo se tiver.
        if (alvo_str.startswith('"') and alvo_str.endswith('"')) or \
           (alvo_str.startswith("'") and alvo_str.endswith("'")):
            alvo_str = alvo_str[1:-1].strip()

        # ─── Resolve categoria. ───
        resultado = _resolver_categoria(ctx.guild, alvo_str)
        if isinstance(resultado, str):
            return await ctx.send(embed=embed_error(resultado))
        categoria = resultado

        # ─── Pega a lista atual de categorias. ───
        categorias = _categorias_ordenadas(ctx.guild)
        if categoria not in categorias:
            return await ctx.send(embed=embed_error(TXT_CATEGORIA_NAO_ACHADA))

        idx_atual = categorias.index(categoria)
        novo_idx = idx_atual

        # ─── Calcula a nova posição conforme a ação. ───
        if acao in ("cima", "up", "subir", "↑", "sobe"):
            if idx_atual == 0:
                return await ctx.send(embed=embed_warning(TXT_JA_NO_TOPO))
            novo_idx = idx_atual - 1

        elif acao in ("baixo", "down", "descer", "↓", "desce"):
            if idx_atual == len(categorias) - 1:
                return await ctx.send(embed=embed_warning(TXT_JA_NO_FUNDO))
            novo_idx = idx_atual + 1

        elif acao in ("top", "topo", "primeiro", "primeira"):
            if idx_atual == 0:
                return await ctx.send(embed=embed_warning(TXT_JA_NO_TOPO))
            novo_idx = 0

        elif acao in ("bottom", "fundo", "ultimo", "último", "ultima", "última"):
            if idx_atual == len(categorias) - 1:
                return await ctx.send(embed=embed_warning(TXT_JA_NO_FUNDO))
            novo_idx = len(categorias) - 1

        else:
            # Deve ser um número (posição exata).
            try:
                novo_idx = int(acao)
            except ValueError:
                return await ctx.send(embed=embed_error(
                    "Ação inválida. Use: `cima`, `baixo`, `top`, "
                    "`bottom` ou um número."
                ))

            if novo_idx < 0 or novo_idx >= len(categorias):
                return await ctx.send(embed=embed_error(
                    f"Posição inválida. Use um número entre "
                    f"`0` e `{len(categorias) - 1}`."
                ))
            if novo_idx == idx_atual:
                return await ctx.send(embed=embed_warning(
                    "Já está nessa posição."
                ))

        # ─── Reordena: remove do lugar antigo, insere no novo. ───
        categorias.pop(idx_atual)
        categorias.insert(novo_idx, categoria)

        # ─── Aplica no Discord. ───
        try:
            await _aplicar_posicoes_categorias(ctx.guild, categorias)
        except Exception as e:
            return await ctx.send(embed=embed_error(f"Erro: {e}"))

        # ─── Resposta. ───
        embed = discord.Embed(
            title=TXT_MOVIDO,
            description=(
                f"**Categoria:** {categoria.name}\n"
                f"**De:** posição `{idx_atual}`\n"
                f"**Para:** posição `{novo_idx}`"
            ),
            color=discord.Color.green(),
        )
        embed.set_footer(text=TXT_FOOTER)
        await ctx.send(embed=embed)

    # ═══════════════════════════════════════════════════════════
    # SUBCOMANDO: >mover canal
    # ═══════════════════════════════════════════════════════════

    @mover.command(name="canal", aliases=["ch", "channel"])
    @commands.has_permissions(manage_channels=True)
    async def mover_canal(self, ctx, canal: discord.abc.GuildChannel, *,
                           acao: str):
        """
        Move um canal dentro da sua categoria (ou pra outra categoria).

        Uso:
          >mover canal #canal cima
          >mover canal #canal baixo
          >mover canal #canal top
          >mover canal #canal bottom
          >mover canal #canal 2
          >mover canal #canal #nova-categoria
        """
        if not canal:
            return await ctx.send(embed=embed_error(TXT_CANAL_NAO_ACHADO))

        # Não é pra mover categorias por aqui.
        if isinstance(canal, discord.CategoryChannel):
            return await ctx.send(embed=embed_error(
                "Use `>mover categoria` pra mover categorias."
            ))

        acao = acao.strip()

        # ─── CASO 1: Mover pra outra categoria? ───
        # Tenta resolver "acao" como categoria.
        resultado_cat = _resolver_categoria(ctx.guild, acao)

        # Verifica se "acao" é uma ação (cima/baixo/top/bottom/número)
        # e NÃO um nome de categoria.
        acao_low = acao.lower()
        eh_acao = acao_low in (
            "cima", "up", "subir", "↑", "sobe",
            "baixo", "down", "descer", "↓", "desce",
            "top", "topo", "primeiro", "primeira",
            "bottom", "fundo", "ultimo", "último", "ultima", "última",
        ) or re.fullmatch(r"\d+", acao_low)

        # Se não é ação E resolveu uma categoria → move pra lá.
        if not eh_acao and isinstance(resultado_cat, discord.CategoryChannel):
            nova_cat = resultado_cat
            try:
                await canal.edit(
                    category=nova_cat,
                    position=len(nova_cat.channels),  # vai pro final
                    reason=f"Mover por {ctx.author}",
                )
            except Exception as e:
                return await ctx.send(embed=embed_error(f"Erro: {e}"))

            return await ctx.send(embed=embed_success(
                f"✅ {canal.mention} movido pra **{nova_cat.name}**."
            ))

        # ─── CASO 2: Mover dentro da categoria atual. ───
        cat_atual = canal.category
        canais = _canais_da_categoria(cat_atual, ctx.guild)

        if canal not in canais:
            return await ctx.send(embed=embed_error(TXT_CANAL_NAO_ACHADO))

        idx_atual = canais.index(canal)
        novo_idx = idx_atual

        # Calcula nova posição.
        if acao_low in ("cima", "up", "subir", "↑", "sobe"):
            if idx_atual == 0:
                return await ctx.send(embed=embed_warning(TXT_JA_NO_TOPO))
            novo_idx = idx_atual - 1

        elif acao_low in ("baixo", "down", "descer", "↓", "desce"):
            if idx_atual == len(canais) - 1:
                return await ctx.send(embed=embed_warning(TXT_JA_NO_FUNDO))
            novo_idx = idx_atual + 1

        elif acao_low in ("top", "topo", "primeiro", "primeira"):
            if idx_atual == 0:
                return await ctx.send(embed=embed_warning(TXT_JA_NO_TOPO))
            novo_idx = 0

        elif acao_low in ("bottom", "fundo", "ultimo", "último", "ultima", "última"):
            if idx_atual == len(canais) - 1:
                return await ctx.send(embed=embed_warning(TXT_JA_NO_FUNDO))
            novo_idx = len(canais) - 1

        else:
            # Deve ser número.
            try:
                novo_idx = int(acao_low)
            except ValueError:
                return await ctx.send(embed=embed_error(
                    "Ação inválida. Use: `cima`, `baixo`, `top`, "
                    "`bottom`, um número ou um nome/ID de categoria."
                ))

            if novo_idx < 0 or novo_idx >= len(canais):
                return await ctx.send(embed=embed_error(
                    f"Posição inválida. Use um número entre "
                    f"`0` e `{len(canais) - 1}`."
                ))
            if novo_idx == idx_atual:
                return await ctx.send(embed=embed_warning(
                    "Já está nessa posição."
                ))

        # Reordena.
        canais.pop(idx_atual)
        canais.insert(novo_idx, canal)

        # Aplica.
        try:
            await _aplicar_posicoes(ctx.guild, canais)
        except Exception as e:
            return await ctx.send(embed=embed_error(f"Erro: {e}"))

        cat_nome = cat_atual.name if cat_atual else "(sem categoria)"
        embed = discord.Embed(
            title=TXT_MOVIDO,
            description=(
                f"**Canal:** {canal.mention}\n"
                f"**Categoria:** {cat_nome}\n"
                f"**De:** posição `{idx_atual}`\n"
                f"**Para:** posição `{novo_idx}`"
            ),
            color=discord.Color.green(),
        )
        embed.set_footer(text=TXT_FOOTER)
        await ctx.send(embed=embed)

    # ═══════════════════════════════════════════════════════════
    # SUBCOMANDO: >mover listar
    # ═══════════════════════════════════════════════════════════

    @mover.command(name="listar", aliases=["ls", "list", "ordem"])
    @commands.has_permissions(manage_channels=True)
    async def mover_listar(self, ctx):
        """Mostra a ordem atual de categorias e canais com índices."""
        categorias = _categorias_ordenadas(ctx.guild)
        linhas = []

        # Canais sem categoria.
        sem_cat = _canais_da_categoria(None, ctx.guild)
        if sem_cat:
            linhas.append("**📂 Sem categoria**")
            for idx, ch in enumerate(sem_cat):
                linhas.append(f"  `{idx}` • {ch.mention}")
            linhas.append("")

        # Cada categoria com seus canais.
        for idx, cat in enumerate(categorias):
            linhas.append(f"**📁 `{idx}` • {cat.name}** (`{cat.id}`)")
            canais = _canais_da_categoria(cat, ctx.guild)
            if not canais:
                linhas.append("  *(vazia)*")
            else:
                for cidx, ch in enumerate(canais):
                    linhas.append(f"  `{cidx}` • {ch.mention}")
            linhas.append("")

        texto = "\n".join(linhas)

        # Se couber num embed (4000 chars), manda embed.
        if len(texto) <= 4000:
            embed = discord.Embed(
                title=TXT_ORDEM_ATUAL,
                description=texto[:4096],
                color=discord.Color.blurple(),
            )
            embed.set_footer(text=(
                "💡 Use >mover categoria <nome/id> <número> • "
                f"{TXT_FOOTER}"
            ))
            await ctx.send(embed=embed)
        else:
            # Se for muito grande, manda como arquivo .txt.
            import io
            file = discord.File(
                io.BytesIO(texto.encode("utf-8")),
                filename="ordem_canais.txt",
            )
            await ctx.send(
                "📋 A ordem está muito grande — mandei como arquivo:",
                file=file,
            )

    # ═══════════════════════════════════════════════════════════
    # ATALHOS: >mover top / >mover bottom
    # ═══════════════════════════════════════════════════════════

    @mover.command(name="top")
    @commands.has_permissions(manage_channels=True)
    async def mover_top(self, ctx, *, alvo: str = None):
        """
        Atalho pra mover algo pro topo.
        Uso: >mover top #canal
             >mover top "Nome Categoria"
             >mover top 123456789
        """
        if not alvo:
            return await ctx.send(embed=embed_error(
                "Use: `>mover top #canal` ou `>mover top \"Nome\"`"
            ))

        # Tenta primeiro como canal (menção <#123>).
        m_canal = re.fullmatch(r"<#(\d+)>", alvo.strip())
        if m_canal:
            ch = ctx.guild.get_channel(int(m_canal.group(1)))
            if ch and not isinstance(ch, discord.CategoryChannel):
                return await self.mover_canal(ctx, ch, "top")

        # Tenta como categoria.
        resultado = _resolver_categoria(ctx.guild, alvo.strip())
        if isinstance(resultado, discord.CategoryChannel):
            return await self.mover_categoria(ctx, argumentos=f"{alvo} top")

        return await ctx.send(embed=embed_error(
            "Não achei canal nem categoria com esse nome/ID."
        ))

    @mover.command(name="bottom", aliases=["fundo"])
    @commands.has_permissions(manage_channels=True)
    async def mover_bottom(self, ctx, *, alvo: str = None):
        """Atalho pra mover pro fundo."""
        if not alvo:
            return await ctx.send(embed=embed_error(
                "Use: `>mover bottom #canal` ou `>mover bottom \"Nome\"`"
            ))

        m_canal = re.fullmatch(r"<#(\d+)>", alvo.strip())
        if m_canal:
            ch = ctx.guild.get_channel(int(m_canal.group(1)))
            if ch and not isinstance(ch, discord.CategoryChannel):
                return await self.mover_canal(ctx, ch, "bottom")

        resultado = _resolver_categoria(ctx.guild, alvo.strip())
        if isinstance(resultado, discord.CategoryChannel):
            return await self.mover_categoria(ctx, argumentos=f"{alvo} bottom")

        return await ctx.send(embed=embed_error(
            "Não achei canal nem categoria com esse nome/ID."
        ))

    # ═══════════════════════════════════════════════════════════
    # TRATAMENTO DE ERROS
    # ═══════════════════════════════════════════════════════════

    async def cog_command_error(self, ctx, error):
        """Captura erros específicos desse cog e mostra mensagens amigáveis."""
        # Se o erro veio "dentro" de outro erro, pega o original.
        if hasattr(error, "original"):
            error = error.original

        if isinstance(error, commands.MissingPermissions):
            return await ctx.send(embed=embed_error(TXT_SEM_PERMISSAO))

        if isinstance(error, commands.MissingRequiredArgument):
            return await ctx.send(embed=embed_error(
                f"❌ Falta o argumento `{error.param.name}`. "
                f"Use `>mover` pra ver como usar."
            ))

        if isinstance(error, commands.ChannelNotFound):
            return await ctx.send(embed=embed_error(TXT_CANAL_NAO_ACHADO))

        if isinstance(error, commands.BadArgument):
            return await ctx.send(embed=embed_error(
                "❌ Argumento inválido. Use `>mover` pra ver como usar."
            ))

        # Erro genérico.
        return await ctx.send(embed=embed_error(f"❌ Erro: {error}"))


# ─── BLOCO 5 — SETUP ───

async def setup(bot):
    """Chamado pelo main.py pra carregar esse cog."""
    if bot.get_cog("ChannelMoverCommands") is None:
        await bot.add_cog(ChannelMoverCommands(bot))