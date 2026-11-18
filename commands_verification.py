# ============================================================
# COMMANDS_VERIFICATION.PY - v9.0
# VERIFICATION GATE COM 5 TIPOS DE CAPTCHA
# ============================================================
# 📌 O QUE ESSE ARQUIVO FAZ?
# Sistema de verificação pra novos membros. Quando alguém entra,
# precisa passar por um desafio antes de ter acesso ao servidor.
#
# 📌 5 TIPOS DE CAPTCHA DISPONÍVEIS:
#   1. button   — só clica no botão (mais fácil)
#   2. math     — resolve uma conta (1+1 = ?)
#   3. image    — identifica letras/números em uma imagem PNG
#   4. emojis   — clica em emojis na ordem correta
#   5. question — responde uma pergunta configurável
#
# 📌 NOVIDADES DA v9.0:
# - 3 novos tipos: image, emojis, question
# - Rate limit: 3 tentativas antes de kick
# - Timeout: captcha expira em X segundos
# - Retrocompatível: configs antigas continuam funcionando
#
# 📌 O QUE MANTÉM DA v8.0:
# - Sistema de botão básico
# - Cargo de verificado e unverified
# - Timeout pra kick se não verificar
# - Bloqueia bots
# ============================================================

# ────────────────────────────────────────────────────────────
# BLOCO 1 — IMPORTS
# ────────────────────────────────────────────────────────────

# "asyncio" = rodar coisas em paralelo (assíncrono).
import asyncio

# "io" = trabalhar com arquivos em memória (BytesIO).
# Usado pra gerar a imagem do captcha sem salvar em disco.
import io

# "random" = números aleatórios (geração do captcha).
import random

# "secrets" = números aleatórios SEGUROS (não previsíveis).
# Usado pra gerar tokens de captcha.
import secrets

# "time" = medir tempo (cooldowns, expiração).
import time

# "datetime" = trabalhar com datas.
# "timedelta" = diferença entre datas.
from datetime import datetime, timedelta, timezone

# "typing" = documentação de tipos.
from typing import Dict, List, Optional, Tuple

# "discord" = biblioteca principal.
import discord

# "commands" = criar comandos e cogs.
# "tasks" = tarefas em loop (background).
from discord.ext import commands, tasks

# "ui" = componentes interativos (botões, selects, modais).
from discord import ui

# ─── Imports do Pillow (geração de imagem) ───
# Pillow = biblioteca de imagens do Python.
# Usada pra gerar o captcha de imagem (letras embaralhadas).
try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False
    Image = None
    ImageDraw = None
    ImageFont = None
    ImageFilter = None

# ─── Imports internos ───

# database = conexão MongoDB.
from database import get_connection_async

# embeds = caixas coloridas prontas.
from embeds import embed_success, embed_error, embed_warning, embed_info

# utils = helpers (cache, etc).
from utils import TTLCache
from protection_safety import automatic_action_allowed


def _now() -> datetime:
    """Retorna a data/hora atual em UTC."""
    return datetime.now(timezone.utc)


# ============================================================
# BLOCO 2 — CONFIGURAÇÃO PADRÃO
# ============================================================
# 📌 RETROCOMPATÍVEL:
# Mantemos todas as chaves antigas da v8.0 pra não quebrar
# configurações já existentes. Só adicionamos chaves NOVAS.

DEFAULT_CONFIG = {
    "enabled": False,

    # ─── Canais e cargos ───
    "channel_id": 0,
    "verified_role_id": 0,
    "unverified_role_id": 0,

    # ─── Método de verificação ───
    # Opções: button / math / image / emojis / question
    "method": "button",

    # ─── Mensagem de verificação ───
    "message_id": 0,
    "emoji": "✅",
    "message_text": (
        "**Bem-vindo(a) ao servidor!**\n\n"
        "Para ter acesso ao servidor, clique no botão abaixo.\n"
        "Isso ajuda a proteger contra raids e bots."
    ),

    # ─── Timeout de verificação ───
    "timeout_minutes": 0,

    # ─── Configurações do captcha ───
    "captcha_difficulty": "easy",      # easy / medium / hard
    "captcha_length": 5,               # tamanho do captcha de imagem/emojis
    "question_text": "Quanto é 2 + 2?",
    "question_answer": "4",

    # ─── Rate limit (NOVO na v9.0) ───
    # Máx de tentativas antes de kickar.
    "max_attempts": 3,

    # Tempo máximo pra resolver o captcha (segundos).
    "captcha_expires_seconds": 180,

    # ─── Kick automático (NOVO na v9.0) ───
    # Se passar do max_attempts, kicka automaticamente?
    "kick_on_max_attempts": True,
}


# ============================================================
# BLOCO 3 — MANAGER (config no MongoDB)
# ============================================================

class VerificationManager:
    # Cache de configs.
    _cache = TTLCache(max_size=200, ttl=60)

    @classmethod
    async def get_config(cls, guild_id: int) -> dict:
        """Pega a config do servidor (cache OU banco), sem bloquear o Discord."""
        cached = cls._cache.get(guild_id)
        if cached is not None:
            return cached

        # Motor executa a leitura de forma assíncrona; o cache continua
        # evitando consultas repetidas durante o TTL já configurado.
        db = await get_connection_async()
        doc = await db["verification_config"].find_one(
            {"guild_id": guild_id}) or {}
        cfg = {**DEFAULT_CONFIG, **doc}

        cls._cache.set(guild_id, cfg)
        return cfg

    @classmethod
    async def update(cls, guild_id: int, key: str, value):
        """Salva uma config no banco antes de invalidar o cache."""
        db = await get_connection_async()
        # Espera a persistência terminar para não confirmar uma alteração
        # que ainda não foi gravada no MongoDB.
        await db["verification_config"].update_one(
            {"guild_id": guild_id},
            {"$set": {key: value}},
            upsert=True,
        )
        # Mantém a estratégia original: a próxima leitura recarrega do banco.
        cls._cache.invalidate(guild_id)

    @classmethod
    def invalidate(cls, guild_id: int):
        """Força recarregar na próxima consulta."""
        cls._cache.invalidate(guild_id)


# ============================================================
# BLOCO 4 — GERADOR DE CAPTCHA (imagem com Pillow)
# ============================================================
# 📌 COMO FUNCIONA?
# 1. Gera N caracteres aleatórios (letras + números)
# 2. Cria uma imagem 300x100 com fundo claro
# 3. Desenha cada caractere em posição e rotação aleatória
# 4. Adiciona "ruído" (linhas e pontos) pra dificultar bots
# 5. Aplica um filtro de blur leve
# 6. Retorna a imagem + o texto original
#
# 📌 POR QUE ISSO É SEGURO?
# Bots de OCR (reconhecimento de texto) conseguem ler imagens
# limpas. Adicionando rotação + ruído + blur, o OCR erra muito
# mais que um humano.

def _gerar_captcha_imagem(length: int = 5) -> Tuple[io.BytesIO, str]:
    """
    Gera um captcha de imagem.

    📌 RETORNA:
    Uma tupla (BytesIO com PNG, texto_original).

    📌 EXEMPLO:
    >>> img_bytes, texto = _gerar_captcha_imagem(5)
    >>> # img_bytes agora contém a imagem
    >>> # texto = "ABC12"
    """
    if not _HAS_PIL:
        # Fallback: sem Pillow, não consegue gerar imagem.
        raise RuntimeError("Pillow não instalado")

    # Caracteres possíveis (letras + números sem confusão tipo 0/O, 1/l).
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

    # Gera o texto aleatório.
    texto = "".join(random.choice(chars) for _ in range(length))

    # Cria a imagem 300x100, fundo branco.
    width, height = 300, 100
    img = Image.new("RGB", (width, height), (245, 245, 245))
    draw = ImageDraw.Draw(img)

    # ─── Adiciona ruído de fundo (pontos aleatórios) ───
    for _ in range(150):
        x = random.randint(0, width)
        y = random.randint(0, height)
        cor = (
            random.randint(180, 230),
            random.randint(180, 230),
            random.randint(180, 230),
        )
        draw.point((x, y), fill=cor)

    # ─── Adiciona linhas aleatórias ───
    for _ in range(6):
        x1 = random.randint(0, width)
        y1 = random.randint(0, height)
        x2 = random.randint(0, width)
        y2 = random.randint(0, height)
        cor = (
            random.randint(150, 200),
            random.randint(150, 200),
            random.randint(150, 200),
        )
        draw.line([(x1, y1), (x2, y2)], fill=cor, width=1)

    # ─── Tenta carregar uma fonte decente ───
    # Se não achar, usa a fonte padrão (feia mas funciona).
    font = None
    try:
        # Fontes comuns em diferentes SOs.
        for fname in ("arial.ttf", "DejaVuSans-Bold.ttf",
                       "DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
            try:
                font = ImageFont.truetype(fname, 50)
                break
            except Exception:
                continue
    except Exception:
        pass

    if font is None:
        font = ImageFont.load_default()

    # ─── Desenha cada caractere ───
    # Espaço entre caracteres = 55px.
    espaco = width // (length + 1)

    for i, char in enumerate(texto):
        # Posição X baseada no índice.
        x = espaco * (i + 1) - 20
        # Posição Y com pequena variação aleatória.
        y = random.randint(15, 35)

        # Cor escura aleatória (varia a tonalidade).
        cor = (
            random.randint(0, 80),
            random.randint(0, 80),
            random.randint(0, 80),
        )

        # Desenha o caractere.
        draw.text((x, y), char, font=font, fill=cor)

    # ─── Adiciona mais ruído por cima ───
    for _ in range(80):
        x = random.randint(0, width)
        y = random.randint(0, height)
        cor = (
            random.randint(0, 100),
            random.randint(0, 100),
            random.randint(0, 100),
        )
        draw.point((x, y), fill=cor)

    # ─── Aplica um blur leve ───
    try:
        img = img.filter(ImageFilter.GaussianBlur(0.5))
    except Exception:
        pass

    # ─── Converte pra BytesIO (arquivo em memória) ───
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    return buffer, texto


def _gerar_captcha_emojis(length: int = 4) -> Tuple[List[str], str]:
    """
    Gera um captcha com sequência de emojis.

    📌 COMO FUNCIONA:
    - Sorteia N emojis distintos
    - Retorna a lista (pro user clicar) + a sequência correta
    - O user precisa clicar NA ORDEM CORRETA

    📌 RETORNA:
    (lista_de_emojis_para_exibir, string_com_sequencia_correta)
    """
    # Pool de emojis (só os que representam objetos claros).
    pool = ["🍎", "🍌", "🍇", "🍓", "🍒", "🍍", "🥝", "🍑",
            "🐶", "🐱", "🐭", "🐰", "🦊", "🐻", "🐼", "🐨",
            "⭐", "🌙", "☀️", "🔥", "❄️", "🌊", "⛰️", "🌈"]

    # Sorteia N emojis sem repetir.
    escolhidos = random.sample(pool, length)

    # Embaralha a ordem de EXIBIÇÃO (pro user não decorar).
    exibicao = list(escolhidos)
    random.shuffle(exibicao)

    # A sequência CORRETA (que o user precisa seguir).
    sequencia = escolhidos

    # Retorna a lista de exibição + sequência correta como string.
    # Formato da sequência: "🍎🍌🍇🍓" (concatenação).
    return exibicao, "".join(sequencia)


# ============================================================
# BLOCO 5 — VIEWS E MODAIS DOS CAPTCHAS
# ============================================================

# ─── 5.1 — Modal de resposta (math e question) ───

class CaptchaAnswerModal(ui.Modal):
    """
    Modal genérico pra resposta do captcha (math ou question).
    """
    def __init__(self, cog, guild_id: int, user_id: int,
                  answer_correto: str, tipo: str = "math"):
        # Título do modal.
        super().__init__(title="Verificação")

        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.answer_correto = answer_correto.lower().strip()
        self.tipo = tipo

        # Campo de input.
        self.input = ui.TextInput(
            label="Sua resposta",
            placeholder="Digite aqui...",
            required=True,
            max_length=100,
        )
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        """Chamado quando o user envia a resposta."""
        resposta = (self.input.value or "").strip().lower()

        # Verifica se acertou (case-insensitive).
        if resposta != self.answer_correto:
            # Errou: incrementa tentativas.
            await self.cog._registrar_tentativa(
                interaction, self.guild_id, self.user_id, False)
            return

        # Acertou: aplica cargo.
        await self.cog._aplicar_verificado(
            interaction, self.guild_id, self.user_id, self.tipo)


# ─── 5.2 — View de emojis ───

class EmojiCaptchaView(ui.View):
    """
    View que mostra os emojis pra clicar NA ORDEM CORRETA.

    📌 COMO FUNCIONA:
    - Mostra N botões, cada um com um emoji diferente
    - O user clica na ordem que o bot pediu
    - Cada clique correto avança; errado reseta
    """
    def __init__(self, cog, guild_id: int, user_id: int,
                  emojis_exibir: List[str], sequencia: str):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.emojis_exibir = emojis_exibir
        self.sequencia = sequencia
        self.progresso = 0  # quantos cliques corretos até agora

        # Adiciona um botão pra cada emoji.
        for i, emoji in enumerate(emojis_exibir):
            btn = ui.Button(
                emoji=emoji,
                style=discord.ButtonStyle.secondary,
                custom_id=f"vc_emoji_{guild_id}_{user_id}_{i}",
                row=i // 5,  # máximo 5 botões por linha
            )
            # Injeta o callback com o emoji específico.
            btn.callback = self._make_callback(emoji)
            self.add_item(btn)

    def _make_callback(self, emoji: str):
        """Cria um callback que sabe qual emoji foi clicado."""
        async def callback(interaction: discord.Interaction):
            # Só o próprio user pode clicar.
            if interaction.user.id != self.user_id:
                return await interaction.response.send_message(
                    "❌ Esse captcha não é seu.", ephemeral=True)

            # Qual emoji deveria ser clicado agora?
            emoji_esperado = self.sequencia[self.progresso]

            if emoji != emoji_esperado:
                # Errou: reseta progresso + incrementa tentativas.
                self.progresso = 0
                await self.cog._registrar_tentativa(
                    interaction, self.guild_id, self.user_id, False)
                return

            # Acertou: avança.
            self.progresso += 1

            # Terminou a sequência?
            if self.progresso >= len(self.sequencia):
                # Sucesso!
                await self.cog._aplicar_verificado(
                    interaction, self.guild_id, self.user_id, "emojis")
                return

            # Ainda não terminou: avisa o progresso.
            await interaction.response.send_message(
                f"✅ Correto! Progresso: **{self.progresso}/"
                f"{len(self.sequencia)}**",
                ephemeral=True)

        return callback


# ─── 5.3 — View principal de verificação (botão) ───

class VerifyButton(ui.Button):
    """
    Botão principal de verificação. Aparece na mensagem fixada
    do canal de verificação.
    """
    def __init__(self):
        super().__init__(
            style=discord.ButtonStyle.success,
            label="✅ Verificar",
            custom_id="verify_button",
        )

    async def callback(self, interaction: discord.Interaction):
        """Chamado quando o user clica no botão."""
        guild = interaction.guild
        member = interaction.user

        # Só membros do servidor.
        if not isinstance(member, discord.Member):
            return await interaction.response.send_message(
                "❌ Apenas membros do servidor podem verificar.",
                ephemeral=True)

        # Bots não verificam.
        if member.bot:
            return await interaction.response.send_message(
                "❌ Bots não podem verificar.", ephemeral=True)

        # Pega o cog.
        cog = interaction.client.get_cog("VerificationCommands")
        if not cog:
            return await interaction.response.send_message(
                "❌ Sistema indisponível.", ephemeral=True)

        cfg = await VerificationManager.get_config(guild.id)

        # Já está verificado?
        verified_role_id = cfg.get("verified_role_id")
        if verified_role_id and member.get_role(int(verified_role_id)):
            return await interaction.response.send_message(
                "✅ Você já está verificado!", ephemeral=True)

        # Verifica se tem timeout ativo (não deixa verificar).
        if member.timed_out_until:
            return await interaction.response.send_message(
                "⏰ Você está de castigo. Aguarde o timeout acabar.",
                ephemeral=True)

        # Inicia o captcha conforme o método.
        await cog._iniciar_captcha(interaction, cfg)


class VerifyView(ui.View):
    """View persistente com botão de verificar."""
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(VerifyButton())


# ============================================================
# BLOCO 6 — COG PRINCIPAL
# ============================================================

class VerificationCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        # ─── Cache de captchas ativos ───
        # Estrutura: {(guild_id, user_id): {"resposta": str, "expires_at": dt, "tipo": str}}
        self._captchas: Dict[tuple, dict] = {}

        # ─── Cache de tentativas ───
        # Estrutura: {(guild_id, user_id): count}
        self._tentativas: Dict[tuple, int] = {}

        # ─── Tasks de timeout (kick se não verificar) ───
        self._timeout_tasks: Dict[int, asyncio.Task] = {}

        # ─── Task que limpa captchas expirados ───
        self._cleanup_task.start()

    def cog_unload(self):
        """Cancela tasks ao descarregar."""
        try:
            self._cleanup_task.cancel()
        except Exception:
            pass

        # Cancela todas as tasks de timeout.
        for task in self._timeout_tasks.values():
            try:
                task.cancel()
            except Exception:
                pass

    # ============================================================
    # TASK: LIMPEZA
    # ============================================================

    @tasks.loop(minutes=5)
    async def _cleanup_task(self):
        """Roda a cada 5min e remove captchas expirados."""
        now = _now()
        chaves_para_remover = []
        for chave, info in self._captchas.items():
            if info["expires_at"] <= now:
                chaves_para_remover.append(chave)
        for chave in chaves_para_remover:
            del self._captchas[chave]

    @_cleanup_task.before_loop
    async def _before_cleanup(self):
        await self.bot.wait_until_ready()

    # ============================================================
    # INICIAR CAPTCHA (roteamento por método)
    # ============================================================

    async def _iniciar_captcha(self, interaction: discord.Interaction,
                                 cfg: dict):
        """
        Inicia o captcha conforme o método configurado.

        📌 MÉTODOS:
        - button   → aplica direto (não precisa resolver nada)
        - math     → abre modal com conta
        - question → abre modal com pergunta
        - image    → gera imagem + abre modal
        - emojis   → mostra view com botões
        """
        guild_id = interaction.guild.id
        user_id = interaction.user.id
        method = cfg.get("method", "button")

        # ─── button: aplica direto ───
        if method == "button":
            return await self._aplicar_verificado(
                interaction, guild_id, user_id, "button")

        # ─── math: gera conta e abre modal ───
        if method == "math":
            difficulty = cfg.get("captcha_difficulty", "easy")
            a, b, op, resposta = self._gerar_matematica(difficulty)

            # Salva o captcha ativo.
            self._salvar_captcha_ativo(guild_id, user_id, resposta,
                                         cfg, "math")

            modal = CaptchaAnswerModal(
                self, guild_id, user_id,
                answer_correto=str(resposta), tipo="math")
            modal.title = f"Verificação: {a} {op} {b} = ?"
            return await interaction.response.send_modal(modal)

        # ─── question: abre modal com pergunta ───
        if method == "question":
            pergunta = cfg.get("question_text", "Quanto é 2 + 2?")
            resposta = cfg.get("question_answer", "4").lower().strip()

            if not resposta:
                return await interaction.response.send_message(
                    "❌ Pergunta mal configurada. Avise um admin.",
                    ephemeral=True)

            self._salvar_captcha_ativo(guild_id, user_id, resposta,
                                         cfg, "question")

            modal = CaptchaAnswerModal(
                self, guild_id, user_id,
                answer_correto=resposta, tipo="question")
            modal.title = f"Verificação: {pergunta}"[:45]
            return await interaction.response.send_modal(modal)

        # ─── image: gera imagem e abre modal ───
        if method == "image":
            if not _HAS_PIL:
                return await interaction.response.send_message(
                    "❌ Captcha de imagem indisponível (Pillow não instalado).\n"
                    "Avise um admin.",
                    ephemeral=True)

            length = int(cfg.get("captcha_length", 5))

            try:
                # Gera imagem em thread separada (não trava o loop).
                loop = asyncio.get_event_loop()
                img_bytes, texto = await loop.run_in_executor(
                    None, _gerar_captcha_imagem, length)
            except Exception as e:
                return await interaction.response.send_message(
                    f"❌ Erro ao gerar captcha: {e}", ephemeral=True)

            # Salva o captcha ativo.
            self._salvar_captcha_ativo(guild_id, user_id,
                                         texto.lower(), cfg, "image")

            # Envia a imagem como ephemeral.
            file = discord.File(img_bytes, filename="captcha.png")

            await interaction.response.send_message(
                content="🔐 **Digite o texto da imagem abaixo:**",
                file=file,
                ephemeral=True)

            # Depois manda o modal (não pode ser no mesmo response).
            # Vamos usar followup.
            modal = CaptchaAnswerModal(
                self, guild_id, user_id,
                answer_correto=texto.lower(), tipo="image")
            modal.title = "Verificação: Digite o texto"
            try:
                await interaction.followup.send(
                    "📝 Clique abaixo pra digitar a resposta:",
                    view=self._make_modal_button(guild_id, user_id,
                                                    texto.lower()),
                    ephemeral=True)
            except Exception:
                pass
            return

        # ─── emojis: gera sequência e mostra view ───
        if method == "emojis":
            length = int(cfg.get("captcha_length", 4))
            emojis_exibir, sequencia = _gerar_captcha_emojis(length)

            # Salva o captcha ativo.
            self._salvar_captcha_ativo(guild_id, user_id, sequencia,
                                         cfg, "emojis")

            # Monta a mensagem com a sequência correta.
            seq_exibida = " → ".join(sequencia)
            msg = (
                f"🔐 **Clique nos emojis na ordem correta:**\n\n"
                f"**Sequência:** {seq_exibida}"
            )

            view = EmojiCaptchaView(
                self, guild_id, user_id, emojis_exibir, sequencia)

            await interaction.response.send_message(
                msg, view=view, ephemeral=True)
            return

        # Fallback: método desconhecido, aplica direto.
        await self._aplicar_verificado(
            interaction, guild_id, user_id, "button")

    def _salvar_captcha_ativo(self, guild_id: int, user_id: int,
                                resposta: str, cfg: dict, tipo: str):
        """Salva um captcha ativo no cache com tempo de expiração."""
        expires_sec = int(cfg.get("captcha_expires_seconds", 180))
        expires_at = _now() + timedelta(seconds=expires_sec)

        self._captchas[(guild_id, user_id)] = {
            "resposta": resposta,
            "expires_at": expires_at,
            "tipo": tipo,
        }

        # Reseta o contador de tentativas (novo captcha).
        # (só reseta se não tiver, pra não burlar rate limit)
        chave = (guild_id, user_id)
        if chave not in self._tentativas:
            self._tentativas[chave] = 0

    def _make_modal_button(self, guild_id: int, user_id: int,
                             resposta: str):
        """Cria uma view com um botão que abre o modal de resposta."""
        cog = self

        class _ModalTrigger(ui.View):
            def __init__(self):
                super().__init__(timeout=300)

            @ui.button(label="📝 Responder", style=discord.ButtonStyle.primary)
            async def responder(self, interaction: discord.Interaction,
                                 button: ui.Button):
                if interaction.user.id != user_id:
                    return await interaction.response.send_message(
                        "❌ Esse captcha não é seu.", ephemeral=True)
                modal = CaptchaAnswerModal(
                    cog, guild_id, user_id, resposta, "image")
                modal.title = "Verificação: Digite o texto"
                await interaction.response.send_modal(modal)

        return _ModalTrigger()

    def _gerar_matematica(self, difficulty: str) -> Tuple[int, int, str, int]:
        """
        Gera uma conta matemática.

        📌 DIFICULDADES:
        - easy   → 1 a 10, só soma
        - medium → 10 a 50, soma ou subtração
        - hard   → 50 a 200, soma/subtração/multiplicação
        """
        if difficulty == "hard":
            a = random.randint(50, 200)
            b = random.randint(50, 200)
            op = random.choice(["+", "-", "*"])
        elif difficulty == "medium":
            a = random.randint(10, 50)
            b = random.randint(10, 50)
            op = random.choice(["+", "-"])
        else:  # easy
            a = random.randint(1, 10)
            b = random.randint(1, 10)
            op = "+"

        # Calcula a resposta.
        if op == "+":
            resposta = a + b
        elif op == "-":
            # Garante que não dá negativo.
            if a < b:
                a, b = b, a
            resposta = a - b
        else:  # *
            # Em hard, multiplicação com números menores.
            a = random.randint(2, 12)
            b = random.randint(2, 12)
            resposta = a * b

        return a, b, op, resposta

    # ============================================================
    # REGISTRAR TENTATIVA (errada)
    # ============================================================

    async def _registrar_tentativa(self, interaction: discord.Interaction,
                                     guild_id: int, user_id: int,
                                     sucesso: bool):
        """
        Registra uma tentativa. Se passar do limite, kicka.

        📌 OBSERVAÇÃO:
        Só é chamado em caso de ERRO. O sucesso vai direto pra
        _aplicar_verificado.
        """
        chave = (guild_id, user_id)
        self._tentativas[chave] = self._tentativas.get(chave, 0) + 1
        tentativas = self._tentativas[chave]

        cfg = await VerificationManager.get_config(guild_id)
        max_attempts = int(cfg.get("max_attempts", 3))

        if tentativas >= max_attempts:
            # Passou do limite: kicka?
            if cfg.get("kick_on_max_attempts", True):
                # Pega o membro e verifica antes de informar que haverá kick.
                guild = self.bot.get_guild(guild_id)
                member = guild.get_member(user_id) if guild else None
                if member and not await automatic_action_allowed(guild_id, member):
                    self._tentativas.pop(chave, None)
                    self._captchas.pop(chave, None)
                    return await interaction.response.send_message(
                        "ℹ️ A proteção automática está desativada ou este membro está na whitelist. Nenhuma expulsão será aplicada.",
                        ephemeral=True)

                await interaction.response.send_message(
                    f"❌ Você errou **{tentativas} vezes**. "
                    f"A expulsão será aplicada se a proteção continuar ativa.",
                    ephemeral=True)

                if member:
                    try:
                        # Última checagem imediatamente antes do kick.
                        if not await automatic_action_allowed(guild_id, member):
                            await interaction.followup.send(
                                "ℹ️ A proteção foi desativada ou a whitelist mudou. Nenhuma expulsão foi aplicada.",
                                ephemeral=True)
                            self._tentativas.pop(chave, None)
                            self._captchas.pop(chave, None)
                            return
                        await member.kick(
                            reason=f"Verificação: {tentativas} tentativas falhas")
                    except Exception:
                        pass

                # Limpa o registro.
                self._tentativas.pop(chave, None)
                self._captchas.pop(chave, None)
                return
            else:
                # Só reseta as tentativas (dá mais chance).
                self._tentativas[chave] = 0
                return await interaction.response.send_message(
                    "❌ Errado! Tente novamente.",
                    ephemeral=True)

        # Ainda tem tentativas.
        restantes = max_attempts - tentativas
        await interaction.response.send_message(
            f"❌ Errado! Tentativas restantes: **{restantes}**",
            ephemeral=True)

    # ============================================================
    # APLICAR VERIFICAÇÃO
    # ============================================================

    async def _aplicar_verificado(self, interaction: discord.Interaction,
                                    guild_id: int, user_id: int,
                                    tipo: str):
        """
        Aplica os cargos de verificado (sucesso!).
        """
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return

        member = guild.get_member(user_id)
        if not member:
            return

        cfg = await VerificationManager.get_config(guild_id)
        verified_role_id = cfg.get("verified_role_id")
        unverified_role_id = cfg.get("unverified_role_id")

        try:
            # Adiciona cargo de verificado.
            if verified_role_id:
                role = guild.get_role(int(verified_role_id))
                if role and role not in member.roles:
                    await member.add_roles(role,
                                            reason=f"Verificação ({tipo})")

            # Remove cargo unverified.
            if unverified_role_id:
                role = guild.get_role(int(unverified_role_id))
                if role and role in member.roles:
                    await member.remove_roles(role,
                                                reason=f"Verificação ({tipo})")

        except Exception:
            pass

        # ─── Log no banco ───
        try:
            # Registra a verificação concluída de forma assíncrona.
            db = await get_connection_async()
            await db["verification_logs"].insert_one({
                "guild_id": guild_id,
                "user_id": user_id,
                "method": tipo,
                "timestamp": _now(),
            })
        except Exception:
            pass

        # ─── Responde ───
        try:
            # Se ainda não respondeu (botão, math, question, image).
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "✅ **Verificado!** Bem-vindo(a)!",
                    ephemeral=True)
            else:
                # Se já respondeu (emojis, followup).
                await interaction.followup.send(
                    "✅ **Verificado!** Bem-vindo(a)!",
                    ephemeral=True)
        except Exception:
            pass

        # ─── Limpa cache ───
        self._captchas.pop((guild_id, user_id), None)
        self._tentativas.pop((guild_id, user_id), None)

    # ============================================================
    # LISTENER: MEMBRO ENTROU
    # ============================================================

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """
        Chamado quando alguém entra. Aplica cargo unverified
        e agenda kick se não verificar em X minutos.
        """
        if member.bot:
            return
        if not await automatic_action_allowed(member.guild.id, member):
            return

        cfg = await VerificationManager.get_config(member.guild.id)
        if not cfg.get("enabled"):
            return

        # ─── Aplica cargo de unverified ───
        unverified = cfg.get("unverified_role_id")
        if unverified:
            try:
                role = member.guild.get_role(int(unverified))
                if role:
                    await member.add_roles(
                        role, reason="Entrada no servidor (não verificado)")
            except Exception:
                pass

        # ─── Agenda kick se não verificar em X minutos ───
        timeout = int(cfg.get("timeout_minutes", 0))
        if timeout > 0:
            async def _timeout_kick():
                """Espera X minutos e kicka se não verificou."""
                await asyncio.sleep(timeout * 60)
                try:
                    # Pega versão atual do member.
                    fresh = member.guild.get_member(member.id)
                    if not fresh:
                        return  # já saiu
                    # Verifica se tá verificado.
                    verified_id = cfg.get("verified_role_id")
                    if verified_id and fresh.get_role(int(verified_id)):
                        return  # já verificou
                    # Revalida toggle global e whitelist após a espera.
                    if not await automatic_action_allowed(member.guild.id, fresh):
                        return
                    # Kicka.
                    await fresh.kick(
                        reason="Não se verificou a tempo")
                except Exception:
                    pass

            # Cancela task antiga se existir.
            old_task = self._timeout_tasks.get(member.id)
            if old_task:
                try:
                    old_task.cancel()
                except Exception:
                    pass

            # Cria a nova task.
            self._timeout_tasks[member.id] = asyncio.create_task(
                _timeout_kick())

    # ============================================================
    # COMANDOS (grupo >verification)
    # ============================================================

    @commands.group(name="verification", aliases=["verify", "verif"],
                    invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def verification(self, ctx, feature: str = None,
                            state: str = None, *, extra: str = None):
        """Comando principal. Sem arg = painel."""
        cfg = await VerificationManager.get_config(ctx.guild.id)

        # Sem arg → painel.
        if not feature:
            return await self._show_panel(ctx, cfg)

        # on/off.
        if feature == "on":
            await VerificationManager.update(ctx.guild.id, "enabled", True)
            return await ctx.send(embed=embed_success(
                "✅ Verificação **ativada**!"))
        if feature == "off":
            await VerificationManager.update(ctx.guild.id, "enabled", False)
            return await ctx.send(embed=embed_success(
                "✅ Verificação **desativada**!"))

        # channel.
        if feature in ("channel", "canal"):
            if not state or not state.startswith("<#"):
                return await ctx.send(embed=embed_error(
                    "❌ Use: `>verification channel #canal`"))
            await VerificationManager.update(ctx.guild.id, "channel_id",
                                         int(state[2:-1]))
            return await ctx.send(embed=embed_success(f"✅ Canal: {state}"))

        # verifiedrole.
        if feature in ("verifiedrole", "vrole"):
            if not state or not state.startswith("<@&"):
                return await ctx.send(embed=embed_error(
                    "❌ Use: `>verification verifiedrole @cargo`"))
            await VerificationManager.update(ctx.guild.id, "verified_role_id",
                                         int(state[3:-1]))
            return await ctx.send(embed=embed_success(
                f"✅ Cargo verificado: {state}"))

        # unverifiedrole.
        if feature in ("unverifiedrole", "urole"):
            if not state or not state.startswith("<@&"):
                return await ctx.send(embed=embed_error(
                    "❌ Use: `>verification unverifiedrole @cargo`"))
            await VerificationManager.update(ctx.guild.id, "unverified_role_id",
                                         int(state[3:-1]))
            return await ctx.send(embed=embed_success(
                f"✅ Cargo unverified: {state}"))

        # method.
        if feature == "method":
            if state not in ("button", "math", "image", "emojis", "question"):
                return await ctx.send(embed=embed_error(
                    "❌ Use: `button`, `math`, `image`, `emojis` ou `question`"))
            await VerificationManager.update(ctx.guild.id, "method", state)
            return await ctx.send(embed=embed_success(f"✅ Método: `{state}`"))

        # timeout.
        if feature == "timeout":
            try:
                await VerificationManager.update(ctx.guild.id, "timeout_minutes",
                                             int(state))
                return await ctx.send(embed=embed_success(
                    f"✅ Timeout: `{state}min`"))
            except Exception:
                return await ctx.send(embed=embed_error("❌ Número inválido."))

        # difficulty.
        if feature == "difficulty":
            if state not in ("easy", "medium", "hard"):
                return await ctx.send(embed=embed_error(
                    "❌ Use: `easy`, `medium` ou `hard`"))
            await VerificationManager.update(ctx.guild.id,
                                         "captcha_difficulty", state)
            return await ctx.send(embed=embed_success(
                f"✅ Dificuldade: `{state}`"))

        # captcha_length.
        if feature in ("captchalength", "length"):
            try:
                n = int(state)
                if n < 3 or n > 10:
                    return await ctx.send(embed=embed_error(
                        "❌ Use entre 3 e 10."))
                await VerificationManager.update(ctx.guild.id,
                                             "captcha_length", n)
                return await ctx.send(embed=embed_success(
                    f"✅ Tamanho do captcha: `{n}`"))
            except Exception:
                return await ctx.send(embed=embed_error("❌ Número inválido."))

        # max_attempts.
        if feature in ("attempts", "tentativas", "maxattempts"):
            try:
                n = int(state)
                if n < 1 or n > 10:
                    return await ctx.send(embed=embed_error(
                        "❌ Use entre 1 e 10."))
                await VerificationManager.update(ctx.guild.id,
                                             "max_attempts", n)
                return await ctx.send(embed=embed_success(
                    f"✅ Tentativas: `{n}`"))
            except Exception:
                return await ctx.send(embed=embed_error("❌ Número inválido."))

        # captcha_expires.
        if feature in ("expires", "expira"):
            try:
                n = int(state)
                if n < 30 or n > 1800:
                    return await ctx.send(embed=embed_error(
                        "❌ Use entre 30 e 1800 segundos."))
                await VerificationManager.update(ctx.guild.id,
                                             "captcha_expires_seconds", n)
                return await ctx.send(embed=embed_success(
                    f"✅ Expira em: `{n}s`"))
            except Exception:
                return await ctx.send(embed=embed_error("❌ Número inválido."))

        # kick_on_max_attempts.
        if feature in ("kickonfail", "kickonmax"):
            if state not in ("on", "off"):
                return await ctx.send(embed=embed_error("❌ Use: `on` ou `off`"))
            enabled = state.lower() == "on"
            await VerificationManager.update(ctx.guild.id,
                                         "kick_on_max_attempts", enabled)
            return await ctx.send(embed=embed_success(
                f"✅ Kick ao falhar: {'ON' if enabled else 'OFF'}"))

        # question.
        if feature == "question":
            if not state:
                return await ctx.send(embed=embed_error(
                    "❌ Use: `>verification question <pergunta> | <resposta>`"))
            # Formato: "pergunta | resposta"
            if "|" not in state:
                return await ctx.send(embed=embed_error(
                    "❌ Use: `>verification question <pergunta> | <resposta>`"))
            pergunta, resposta = state.split("|", 1)
            await VerificationManager.update(ctx.guild.id,
                                         "question_text", pergunta.strip())
            await VerificationManager.update(ctx.guild.id,
                                         "question_answer", resposta.strip())
            return await ctx.send(embed=embed_success(
                f"✅ Pergunta configurada."))

        # send/setup: envia a mensagem de verificação.
        if feature in ("send", "setup"):
            return await self._send_verify_message(ctx, cfg)

        # stats.
        if feature == "stats":
            return await self._show_stats(ctx)

        return await ctx.send(embed=embed_error("❌ Feature inválida."))

    async def _send_verify_message(self, ctx, cfg: dict):
        """Envia a mensagem com o botão de verificar no canal."""
        channel_id = cfg.get("channel_id")
        if not channel_id:
            return await ctx.send(embed=embed_error(
                "❌ Configure o canal: `>verification channel #canal`"))

        channel = ctx.guild.get_channel(int(channel_id))
        if not channel:
            return await ctx.send(embed=embed_error(
                "❌ Canal não encontrado."))

        # Monta o embed.
        embed = discord.Embed(
            title="✅ VERIFICAÇÃO",
            description=cfg.get("message_text",
                                 DEFAULT_CONFIG["message_text"]),
            color=discord.Color.green(),
            timestamp=_now())

        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)

        # Envia com a view.
        msg = await channel.send(embed=embed, view=VerifyView())

        # Salva o ID da mensagem.
        await VerificationManager.update(ctx.guild.id, "message_id", msg.id)

        await ctx.send(embed=embed_success(
            f"✅ Mensagem enviada em {channel.mention}"))

    async def _show_stats(self, ctx):
        """Mostra estatísticas de verificação."""
        # Consulta as estatísticas com Motor, sem bloquear o event loop.
        db = await get_connection_async()

        total = await db["verification_logs"].count_documents(
            {"guild_id": ctx.guild.id})

        cutoff = _now() - timedelta(hours=24)
        last_24h = await db["verification_logs"].count_documents({
            "guild_id": ctx.guild.id,
            "timestamp": {"$gte": cutoff},
        })

        # Mantém exatamente os estágios originais da agregação e materializa
        # o cursor assíncrono antes de montar o embed.
        por_metodo = await db["verification_logs"].aggregate([
            {"$match": {"guild_id": ctx.guild.id}},
            {"$group": {"_id": "$method", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
        ]).to_list(length=None)

        embed = discord.Embed(
            title="📊 ESTATÍSTICAS DE VERIFICAÇÃO",
            color=discord.Color.green(),
            timestamp=_now())

        embed.add_field(name="Total verificado", value=str(total),
                         inline=True)
        embed.add_field(name="Últimas 24h", value=str(last_24h),
                         inline=True)

        if por_metodo:
            linhas = [f"• `{m['_id']}`: {m['count']}"
                       for m in por_metodo]
            embed.add_field(name="Por método", value="\n".join(linhas),
                             inline=False)

        await ctx.send(embed=embed)

    async def _show_panel(self, ctx, cfg: dict):
        """Mostra o painel de configuração."""
        def onoff(v):
            return "✅" if v else "❌"

        channel_str = (f"<#{cfg['channel_id']}>"
                        if cfg.get("channel_id") else "❌")
        verified_str = (f"<@&{cfg['verified_role_id']}>"
                         if cfg.get("verified_role_id") else "❌")
        unverified_str = (f"<@&{cfg['unverified_role_id']}>"
                           if cfg.get("unverified_role_id") else "❌")

        # Nome do método formatado.
        metodo_label = {
            "button": "🔘 Botão",
            "math": "🔢 Matemática",
            "image": "🖼️ Imagem",
            "emojis": "😀 Emojis",
            "question": "❓ Pergunta",
        }.get(cfg.get("method", "button"), cfg.get("method"))

        embed = discord.Embed(
            title="✅ SISTEMA DE VERIFICAÇÃO v9.0",
            description=(
                f"**Status:** {onoff(cfg['enabled'])}\n"
                f"**Canal:** {channel_str}\n"
                f"**Método:** {metodo_label}\n"
                f"**Cargo verificado:** {verified_str}\n"
                f"**Cargo unverified:** {unverified_str}\n"
                f"**Timeout de entrada:** `{cfg['timeout_minutes']}min`\n"
                f"**Dificuldade:** `{cfg['captcha_difficulty']}`\n"
                f"**Tamanho do captcha:** `{cfg['captcha_length']}`\n"
                f"**Máx tentativas:** `{cfg['max_attempts']}`\n"
                f"**Expira em:** `{cfg['captcha_expires_seconds']}s`\n"
                f"**Kick ao falhar:** {onoff(cfg['kick_on_max_attempts'])}"
            ),
            color=discord.Color.green(),
            timestamp=_now())

        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)

        embed.add_field(
            name="📌 Comandos",
            value=(
                "`>verification on/off`\n"
                "`>verification channel #canal`\n"
                "`>verification verifiedrole @cargo`\n"
                "`>verification unverifiedrole @cargo`\n"
                "`>verification method button/math/image/emojis/question`\n"
                "`>verification timeout <min>`\n"
                "`>verification difficulty easy/medium/hard`\n"
                "`>verification captchalength <n>`\n"
                "`>verification attempts <n>`\n"
                "`>verification expires <s>`\n"
                "`>verification kickonfail on/off`\n"
                "`>verification question <pergunta> | <resposta>`\n"
                "`>verification setup` — envia mensagem\n"
                "`>verification stats`"
            ),
            inline=False)

        await ctx.send(embed=embed)

    # ─── Teste de captcha ───

    @verification.command(name="test")
    @commands.has_permissions(administrator=True)
    async def verification_test(self, ctx):
        """Testa o captcha configurado (envia aqui no canal)."""
        cfg = await VerificationManager.get_config(ctx.guild.id)

        # Simula um click no botão de verificação.
        # (envia uma mensagem ephemeral com o captcha atual).
        method = cfg.get("method", "button")

        if method == "image":
            if not _HAS_PIL:
                return await ctx.send(embed=embed_error(
                    "❌ Pillow não instalado. Instale com "
                    "`pip install Pillow`"))

            length = int(cfg.get("captcha_length", 5))
            try:
                loop = asyncio.get_event_loop()
                img_bytes, _ = await loop.run_in_executor(
                    None, _gerar_captcha_imagem, length)
                file = discord.File(img_bytes, filename="captcha_test.png")
                await ctx.send("🧪 **Teste de captcha (image):**", file=file)
            except Exception as e:
                await ctx.send(embed=embed_error(f"❌ Erro: {e}"))

        elif method == "emojis":
            length = int(cfg.get("captcha_length", 4))
            emojis_exibir, sequencia = _gerar_captcha_emojis(length)
            seq_str = " → ".join(sequencia)
            await ctx.send(
                f"🧪 **Teste de captcha (emojis):**\n"
                f"**Sequência:** {seq_str}\n"
                f"**Emojis disponíveis:** {' '.join(emojis_exibir)}")

        elif method == "math":
            difficulty = cfg.get("captcha_difficulty", "easy")
            a, b, op, resposta = self._gerar_matematica(difficulty)
            await ctx.send(
                f"🧪 **Teste de captcha (math):**\n"
                f"`{a} {op} {b} = ?` (resposta: `{resposta}`)")

        elif method == "question":
            await ctx.send(
                f"🧪 **Teste de captcha (question):**\n"
                f"**Pergunta:** {cfg.get('question_text', '?')}\n"
                f"**Resposta:** `{cfg.get('question_answer', '?')}`")

        else:
            await ctx.send(
                f"🧪 **Teste de captcha (button):**\n"
                f"Nenhum desafio — só clica e verifica.")


# ============================================================
# BLOCO 7 — SETUP
# ============================================================

async def setup(bot):
    """Chamado pelo main.py pra carregar esse cog."""
    if bot.get_cog("VerificationCommands") is None:
        await bot.add_cog(VerificationCommands(bot))