/**
 * <tverbum-editor> - Web Component Standalone Importável (Shadow DOM)
 * Pode ser importado em qualquer aplicação web (React, Vue, Next.js, Laravel, Django, PHP, HTML)
 * para revisão assistida de atas geradas pelo tVerbum.
 *
 * Uso:
 *   <script src="http://localhost:8000/tverbum-editor.js"></script>
 *   <tverbum-editor id="meu-editor" api-base="http://localhost:8000"></tverbum-editor>
 *
 * Métodos públicos:
 *   editor.setMinute(minuteJson)  -> Carrega o JSON completo retornado pelo tVerbum
 *   editor.getMinute()            -> Retorna o JSON atualizado com as edições feitas na tela
 *   editor.getBodyText()          -> Retorna apenas a string do corpo da ata revisado
 *   editor.validateAndNormalize() -> Aplica o validador cartorial (extenso, abreviações, parágrafo único)
 *
 * Eventos disparados:
 *   'tverbum-change' -> detail: { body, title, wordCount, charCount }
 *   'tverbum-save'   -> detail: MinuteOutput completo revisado
 */

class TVerbumEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._minute = null;
    this._highlightsActive = false;
    this._diffActive = false;
    this._autosaveTimer = null;
  }

  static get observedAttributes() {
    return ['api-base', 'show-save-button'];
  }

  get apiBase() {
    return (this.getAttribute('api-base') || '').replace(/\/$/, '');
  }

  connectedCallback() {
    this._renderShell();
  }

  setMinute(minuteData) {
    this._minute = JSON.parse(JSON.stringify(minuteData));
    if (!this._minute.original_body && this._minute.body) {
      this._minute.original_body = this._minute.body;
    }
    if (!this._minute.document_status) {
      this._minute.document_status = 'rascunho_ia';
    }
    this._highlightsActive = false;
    this._diffActive = false;
    this._updateView();
    this._checkLocalDraft();
  }

  getMinute() {
    if (!this._minute) return null;
    this._syncFromDOM();
    return JSON.parse(JSON.stringify(this._minute));
  }

  getBodyText() {
    const bodyEl = this.shadowRoot.getElementById('tv-body');
    return bodyEl ? bodyEl.innerText.trim() : '';
  }

  setBodyText(text) {
    if (!this._minute) {
      this._minute = { title: 'ATA DE SESSÃO', body: text, original_body: text, document_status: 'rascunho_ia', metadata: {} };
    } else {
      this._minute.body = text;
    }
    this._updateView();
  }

  _syncFromDOM() {
    if (!this._minute || this._diffActive) return;
    const titleEl = this.shadowRoot.getElementById('tv-title');
    const bodyEl = this.shadowRoot.getElementById('tv-body');
    if (titleEl) this._minute.title = titleEl.innerText.trim();
    if (bodyEl) this._minute.body = bodyEl.innerText.trim();
    this._minute.full_text = [
      this._minute.header || this._minute.title,
      this._minute.body,
      this._minute.signatures || ''
    ].filter(Boolean).join('\n\n');
  }

  _showToast(msg, duration = 4000) {
    const msgBox = this.shadowRoot.getElementById('tv-toast');
    if (msgBox) {
      msgBox.innerText = msg;
      msgBox.style.display = 'block';
      clearTimeout(this._toastTimer);
      this._toastTimer = setTimeout(() => { msgBox.style.display = 'none'; }, duration);
    }
  }

  _checkLocalDraft() {
    if (!this._minute || !this._minute.id) return;
    const banner = this.shadowRoot.getElementById('tv-draft-banner');
    if (!banner) return;

    try {
      const raw = localStorage.getItem('tverbum_draft_' + this._minute.id);
      if (!raw) {
        banner.style.display = 'none';
        return;
      }
      const draft = JSON.parse(raw);
      if (draft && draft.body && draft.body.trim() !== (this._minute.body || '').trim()) {
        const timeStr = draft.saved_at ? new Date(draft.saved_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
        const infoSpan = this.shadowRoot.getElementById('tv-draft-info');
        if (infoSpan) {
          infoSpan.innerText = `⚠️ Encontrado rascunho salvo localmente neste navegador (${timeStr}). Deseja restaurar as edições?`;
        }
        banner.style.display = 'flex';
      } else {
        banner.style.display = 'none';
      }
    } catch (e) {
      banner.style.display = 'none';
    }
  }

  _restoreLocalDraft() {
    if (!this._minute || !this._minute.id) return;
    try {
      const raw = localStorage.getItem('tverbum_draft_' + this._minute.id);
      if (!raw) return;
      const draft = JSON.parse(raw);
      if (draft.body) this._minute.body = draft.body;
      if (draft.title) this._minute.title = draft.title;
      if (draft.document_status) this._minute.document_status = draft.document_status;
      this._updateView();
      const banner = this.shadowRoot.getElementById('tv-draft-banner');
      if (banner) banner.style.display = 'none';
      this._showToast('💾 Rascunho local restaurado com sucesso!');
    } catch (e) {}
  }

  _discardLocalDraft() {
    if (!this._minute || !this._minute.id) return;
    try {
      localStorage.removeItem('tverbum_draft_' + this._minute.id);
      const banner = this.shadowRoot.getElementById('tv-draft-banner');
      if (banner) banner.style.display = 'none';
      this._showToast('Rascunho local descartado.');
    } catch (e) {}
  }

  _scheduleAutosave() {
    clearTimeout(this._autosaveTimer);
    this._autosaveTimer = setTimeout(() => {
      this._performAutosave();
    }, 700);
  }

  _performAutosave() {
    if (!this._minute || !this._minute.id || this._diffActive) return;
    this._syncFromDOM();
    const draft = {
      body: this._minute.body,
      title: this._minute.title,
      document_status: this._minute.document_status || 'rascunho_ia',
      saved_at: Date.now(),
    };
    try {
      localStorage.setItem('tverbum_draft_' + this._minute.id, JSON.stringify(draft));
      const badge = this.shadowRoot.getElementById('tv-autosave-badge');
      if (badge) {
        const timeStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        badge.innerText = `💾 Salvo (${timeStr})`;
      }
    } catch (e) {}
  }

  async setDocumentStatus(status) {
    if (!this._minute) return;
    this._minute.document_status = status;
    this._updateStatusBadge();
    this.dispatchEvent(new CustomEvent('tverbum-status-change', {
      detail: { status, minute_id: this._minute.id },
      bubbles: true,
      composed: true,
    }));

    if (this.apiBase && this._minute.id && this._minute.id.length > 5) {
      try {
        await fetch(`${this.apiBase}/api/session/${this._minute.id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ document_status: status }),
        });
      } catch (e) {}
    }
    this._performAutosave();
  }

  _updateStatusBadge() {
    const sel = this.shadowRoot.getElementById('tv-status-select');
    const badge = this.shadowRoot.getElementById('tv-status-badge');
    const status = (this._minute && this._minute.document_status) || 'rascunho_ia';
    if (sel) sel.value = status;
    if (badge) {
      badge.className = `tv-status-badge status-${status}`;
    }
  }

  _toggleDiff() {
    if (!this._minute) return;
    this._syncFromDOM();
    const bodyEl = this.shadowRoot.getElementById('tv-body');
    const diffBtn = this.shadowRoot.getElementById('btn-diff');
    if (!bodyEl || !diffBtn) return;

    this._diffActive = !this._diffActive;
    if (!this._diffActive) {
      bodyEl.innerText = this._minute.body;
      bodyEl.contentEditable = "true";
      diffBtn.classList.remove('tv-btn-active');
      diffBtn.innerText = '🔍 Ver Alterações (Diff)';
      return;
    }

    const original = (this._minute.original_body || '').trim();
    const current = (this._minute.body || '').trim();

    if (!original) {
      this._showToast('Texto original da IA não disponível para comparação.');
      this._diffActive = false;
      return;
    }

    if (original === current) {
      this._showToast('ℹ️ O texto atual é idêntico à minuta original gerada pela IA.');
      this._diffActive = false;
      return;
    }

    const diff = this._computeWordDiff(original, current);
    bodyEl.innerHTML = diff.html;
    bodyEl.contentEditable = "false";
    diffBtn.classList.add('tv-btn-active');
    diffBtn.innerText = `✕ Fechar Diff (+${diff.insCount} / -${diff.delCount})`;
  }

  _computeWordDiff(textA, textB) {
    const wordsA = textA.split(/\s+/).filter(Boolean);
    const wordsB = textB.split(/\s+/).filter(Boolean);
    const m = wordsA.length;
    const n = wordsB.length;

    // Se um dos textos for excessivamente grande (> 3000 palavras), faz fallback rápido
    if (m * n > 4000000) {
      return { html: this._escapeHtml(textB), insCount: 0, delCount: 0 };
    }

    const dp = Array.from({ length: m + 1 }, () => new Uint16Array(n + 1));
    for (let i = 0; i < m; i++) {
      for (let j = 0; j < n; j++) {
        if (wordsA[i] === wordsB[j]) {
          dp[i + 1][j + 1] = dp[i][j] + 1;
        } else {
          dp[i + 1][j + 1] = Math.max(dp[i + 1][j], dp[i][j + 1]);
        }
      }
    }

    let i = m;
    let j = n;
    const chunks = [];
    while (i > 0 || j > 0) {
      if (i > 0 && j > 0 && wordsA[i - 1] === wordsB[j - 1]) {
        chunks.push({ type: 'same', word: wordsA[i - 1] });
        i--;
        j--;
      } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
        chunks.push({ type: 'ins', word: wordsB[j - 1] });
        j--;
      } else if (i > 0 && (j === 0 || dp[i][j - 1] < dp[i - 1][j])) {
        chunks.push({ type: 'del', word: wordsA[i - 1] });
        i--;
      }
    }
    chunks.reverse();

    let html = '';
    let insCount = 0;
    let delCount = 0;
    for (const c of chunks) {
      const esc = this._escapeHtml(c.word);
      if (c.type === 'same') {
        html += esc + ' ';
      } else if (c.type === 'ins') {
        html += `<ins class="tv-diff-ins">${esc}</ins> `;
        insCount++;
      } else if (c.type === 'del') {
        html += `<del class="tv-diff-del">${esc}</del> `;
        delCount++;
      }
    }
    return { html: html.trim(), insCount, delCount };
  }

  async validateAndNormalize(options = {}) {
    this._syncFromDOM();
    if (!this._minute || !this._minute.body) return null;

    const payload = {
      text: this._minute.body,
      single_paragraph: options.single_paragraph !== undefined ? options.single_paragraph : true,
      paragraph_count: options.paragraph_count || 1,
      numbers_in_words: options.numbers_in_words !== undefined ? options.numbers_in_words : true,
      allow_abbreviations: options.allow_abbreviations !== undefined ? options.allow_abbreviations : false,
    };

    const btn = this.shadowRoot.getElementById('btn-validate');
    if (btn) btn.innerText = 'Validando...';

    try {
      const res = await fetch(`${this.apiBase}/api/validate-text`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error('Falha ao validar texto');
      const data = await res.json();
      this._minute.body = data.normalized_text;
      this.shadowRoot.getElementById('tv-body').innerText = data.normalized_text;
      this._updateStats();

      const msgBox = this.shadowRoot.getElementById('tv-toast');
      if (msgBox) {
        if (data.corrections_applied && data.corrections_applied.length > 0) {
          msgBox.innerText = '✅ Correções aplicadas: ' + data.corrections_applied.join(' | ');
        } else {
          msgBox.innerText = '✅ Texto 100% em conformidade com o padrão cartorial!';
        }
        msgBox.style.display = 'block';
        setTimeout(() => { msgBox.style.display = 'none'; }, 5000);
      }
      return data;
    } catch (e) {
      alert('Erro ao validar texto: ' + e.message);
      return null;
    } finally {
      if (btn) btn.innerText = '✓ Normalizar Padrão Cartorial';
    }
  }

  async refineSelectedSnippet(instruction) {
    const sel = this.shadowRoot.getSelection
      ? this.shadowRoot.getSelection()
      : window.getSelection();
    const selectedText = sel ? sel.toString().trim() : '';

    if (!selectedText || selectedText.length < 5) {
      alert('Selecione primeiro um trecho ou frase dentro do texto da ata para reescrever com IA.');
      return;
    }

    const btn = this.shadowRoot.getElementById('btn-refine');
    if (btn) btn.innerText = 'Reescrevendo...';

    try {
      const res = await fetch(`${this.apiBase}/api/refine-snippet`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          snippet: selectedText,
          instruction: instruction || 'Tornar mais formal, impessoal e no padrão legislativo cartorial, mantendo números por extenso.',
          numbers_in_words: true,
          allow_abbreviations: false,
        }),
      });
      if (!res.ok) throw new Error('Falha ao refinar trecho');
      const data = await res.json();

      const bodyEl = this.shadowRoot.getElementById('tv-body');
      const currentBody = bodyEl.innerText;
      if (currentBody.includes(selectedText)) {
        bodyEl.innerText = currentBody.replace(selectedText, data.refined_snippet);
        this._syncFromDOM();
        this._updateStats();
      }
    } catch (e) {
      alert('Erro ao refinar trecho: ' + e.message);
    } finally {
      if (btn) btn.innerText = '✨ Reescrever Seleção com IA';
    }
  }

  async quickLearnSelectedName() {
    this._syncFromDOM();
    const sel = this.shadowRoot.getSelection
      ? this.shadowRoot.getSelection()
      : window.getSelection();
    let wrongText = sel ? sel.toString().trim() : '';

    if (!wrongText) {
      wrongText = prompt('Digite o nome que saiu com a grafia ERRADA no texto (ou deixe em branco para apenas cadastrar um nome novo):', '');
      if (wrongText === null) return;
      wrongText = wrongText.trim();
    }

    const correctName = prompt(
      wrongText
        ? `Qual é a grafia OFICIAL correta para substituir "${wrongText}"?`
        : 'Digite a grafia OFICIAL correta do nome para adicionar à Biblioteca:',
      wrongText
    );
    if (!correctName || !correctName.trim()) return;
    const cleanCorrect = correctName.trim();

    try {
      const res = await fetch(`${this.apiBase}/api/names/quick-learn`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          wrong_text: wrongText,
          correct_name: cleanCorrect,
          category: 'parlamentar',
        }),
      });
      if (!res.ok) throw new Error('Falha ao salvar nome na Biblioteca');

      // Se havia texto errado e ata aberta, substitui todas as ocorrências imediatamente
      if (wrongText && this._minute) {
        const bodyEl = this.shadowRoot.getElementById('tv-body');
        if (bodyEl && bodyEl.innerText.includes(wrongText)) {
          bodyEl.innerText = bodyEl.innerText.split(wrongText).join(cleanCorrect);
        }
        this._syncFromDOM();
        // Chama validateAndNormalize para aplicar em todo o texto com as regras atualizadas
        await this.validateAndNormalize();
      }

      const msgBox = this.shadowRoot.getElementById('tv-toast');
      if (msgBox) {
        msgBox.innerText = wrongText
          ? `✅ Aprendido na Biblioteca: "${wrongText}" → "${cleanCorrect}". Todas as ocorrências foram corrigidas!`
          : `✅ Nome oficial "${cleanCorrect}" salvo na Biblioteca de Nomes!`;
        msgBox.style.display = 'block';
        setTimeout(() => { msgBox.style.display = 'none'; }, 5500);
      }

      this.dispatchEvent(new CustomEvent('tverbum-name-learned', {
        detail: { wrongText, correctName: cleanCorrect },
        bubbles: true,
        composed: true,
      }));
    } catch (e) {
      alert('Erro ao salvar na Biblioteca de Nomes: ' + e.message);
    }
  }

  _toggleHighlights() {
    if (!this._minute) return;
    this._syncFromDOM();
    const bodyEl = this.shadowRoot.getElementById('tv-body');
    const warnings = this._minute.review_warnings || [];
    if (!warnings.length) return;

    this._highlightsActive = !this._highlightsActive;
    if (!this._highlightsActive) {
      bodyEl.innerText = this._minute.body;
      return;
    }

    let html = this._escapeHtml(this._minute.body);
    warnings.forEach(w => {
      if (!w.term || w.term.length < 3) return;
      const escapedTerm = this._escapeHtml(w.term);
      const tooltip = this._escapeHtml(`${w.reason}${w.timestamp ? ' [' + w.timestamp + ']' : ''}`);
      html = html.split(escapedTerm).join(
        `<mark class="tv-mark" title="${tooltip}">${escapedTerm}</mark>`
      );
    });
    bodyEl.innerHTML = html;
  }

  _escapeHtml(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  _timestampToSeconds(ts) {
    if (!ts) return 0;
    const parts = ts.replace(/[^\d:]/g, '').split(':').map(Number);
    if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
    if (parts.length === 2) return parts[0] * 60 + parts[1];
    return parts[0] || 0;
  }

  _updateStats() {
    const bodyEl = this.shadowRoot.getElementById('tv-body');
    const text = bodyEl ? bodyEl.innerText.trim() : '';
    const words = text ? text.split(/\s+/).length : 0;
    const chars = text.length;
    const statEl = this.shadowRoot.getElementById('tv-stats');
    if (statEl) {
      let extra = '';
      if (this._minute && this._minute.metadata && (this._minute.metadata.provider_used || this._minute.metadata.model_used)) {
        const prov = this._minute.metadata.provider_used || '';
        const mod = this._minute.metadata.model_used || '';
        extra = ` · IA: ${prov} (${mod})`;
      }
      statEl.innerText = `${words} palavras · ${chars} caracteres${extra}`;
    }
    this.dispatchEvent(new CustomEvent('tverbum-change', {
      detail: {
        body: text,
        title: this.shadowRoot.getElementById('tv-title')?.innerText.trim() || '',
        wordCount: words,
        charCount: chars,
      },
      bubbles: true,
      composed: true,
    }));
  }

  _updateView() {
    const emptyEl = this.shadowRoot.getElementById('tv-empty');
    const sheetEl = this.shadowRoot.getElementById('tv-sheet-content');
    const panelsEl = this.shadowRoot.getElementById('tv-Structured-panels');

    if (!this._minute) {
      emptyEl.style.display = 'flex';
      sheetEl.style.display = 'none';
      panelsEl.style.display = 'none';
      return;
    }

    emptyEl.style.display = 'none';
    sheetEl.style.display = 'block';
    panelsEl.style.display = 'grid';

    // Logo
    const logoEl = this.shadowRoot.getElementById('tv-logo');
    if (this._minute.logo_url) {
      logoEl.src = this._minute.logo_url;
      logoEl.style.display = 'block';
    } else {
      logoEl.style.display = 'none';
    }

    // Header
    const hdrEl = this.shadowRoot.getElementById('tv-header');
    hdrEl.innerHTML = '';
    if (this._minute.state) {
      const p = document.createElement('div');
      p.className = 'hdr-state';
      p.innerText = this._minute.state.toUpperCase();
      hdrEl.appendChild(p);
    }
    if (this._minute.organization_name) {
      const p = document.createElement('div');
      p.className = 'hdr-org';
      p.innerText = this._minute.organization_name.toUpperCase();
      hdrEl.appendChild(p);
    }
    if (this._minute.organization_details) {
      this._minute.organization_details.split('\n').forEach(line => {
        if (!line.trim()) return;
        const p = document.createElement('div');
        p.className = 'hdr-det';
        p.innerText = line.trim();
        hdrEl.appendChild(p);
      });
    }

    this.shadowRoot.getElementById('tv-title').innerText = this._minute.title || '';
    this.shadowRoot.getElementById('tv-body').innerText = this._minute.body || '';

    const sigEl = this.shadowRoot.getElementById('tv-signatures');
    sigEl.style.display = this._minute.signatures ? 'grid' : 'none';

    // Warnings badge
    const warnings = this._minute.review_warnings || [];
    const warnBtn = this.shadowRoot.getElementById('btn-warnings');
    if (warnings.length > 0) {
      warnBtn.style.display = 'inline-flex';
      warnBtn.innerText = `⚠️ Destacar Revisão (${warnings.length})`;
    } else {
      warnBtn.style.display = 'none';
    }

    // Extracted Data Panel
    const extEl = this.shadowRoot.getElementById('tv-extracted-list');
    const ext = this._minute.extracted_data || {};
    const matters = ext.voted_matters || [];
    extEl.innerHTML = `
      <div><strong>Data ISO:</strong> ${ext.session_date_iso || 'Não identificada'}</div>
      <div><strong>Horário:</strong> ${ext.start_time || '--:--'} às ${ext.end_time || '--:--'}</div>
      <div><strong>Presidente:</strong> ${ext.president || '-'}</div>
      <div><strong>Presentes (${(ext.present_members || []).length}):</strong> ${(ext.present_members || []).join(', ') || '-'}</div>
      <div style="margin-top:6px;"><strong>Matérias na Ordem do Dia (${matters.length}):</strong></div>
      <ul style="margin:4px 0 0 16px;padding:0;">
        ${matters.map(m => `<li><strong>${this._escapeHtml(m.matter)}</strong>: ${this._escapeHtml(m.result || m.summary)}</li>`).join('') || '<li>Nenhuma matéria listada</li>'}
      </ul>
    `;

    // Timeline Panel
    const tlEl = this.shadowRoot.getElementById('tv-timeline-list');
    const timeline = this._minute.timeline_map || [];
    const videoUrl = (this._minute.metadata && this._minute.metadata.url) || '';
    if (timeline.length > 0) {
      tlEl.innerHTML = timeline.map(item => {
        const secs = this._timestampToSeconds(item.timestamp);
        const link = videoUrl ? `${videoUrl}&t=${secs}s` : '#';
        return `
          <div class="tl-item">
            <a href="${link}" target="_blank" class="tl-badge" title="Abrir vídeo neste minuto">[${this._escapeHtml(item.timestamp)}]</a>
            <div>
              <div style="font-weight:600;color:#e2e8f0;">${this._escapeHtml(item.phase)}</div>
              <div style="color:#94a3b8;font-size:11px;">${this._escapeHtml(item.summary)}</div>
            </div>
          </div>
        `;
      }).join('');
    } else {
      tlEl.innerHTML = '<div style="color:#64748b;">Nenhum marcador temporal disponível.</div>';
    }

    this._updateStats();
    this._updateStatusBadge();

    const stampEl = this.shadowRoot.getElementById('tv-integrity-stamp');
    const hashEl = this.shadowRoot.getElementById('tv-stamp-hash');
    if (stampEl && hashEl) {
      if (this._minute.integrity_hash) {
        stampEl.style.display = 'block';
        hashEl.innerText = `Chave SHA-256: ${this._minute.integrity_hash}`;
      } else {
        stampEl.style.display = 'none';
      }
    }
  }

  _renderShell() {
    const showSave = this.hasAttribute('show-save-button');
    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
          font-family: 'Inter', system-ui, -apple-system, sans-serif;
          color: #0f172a;
        }
        .tv-toolbar {
          background: #020617;
          border: 1px solid #1e293b;
          border-radius: 12px;
          padding: 10px 14px;
          display: flex;
          flex-wrap: wrap;
          align-items: center;
          justify-content: space-between;
          gap: 8px;
          margin-bottom: 12px;
        }
        .tv-btn-group {
          display: flex;
          flex-wrap: wrap;
          align-items: center;
          gap: 6px;
        }
        .tv-btn {
          background: #1e293b;
          color: #e2e8f0;
          border: 1px solid #334155;
          border-radius: 8px;
          padding: 6px 11px;
          font-size: 12px;
          font-weight: 500;
          cursor: pointer;
          transition: all 0.15s;
        }
        .tv-btn:hover { background: #334155; }
        .tv-btn-amber {
          background: rgba(245, 158, 11, 0.15);
          color: #fbbf24;
          border-color: rgba(245, 158, 11, 0.35);
        }
        .tv-btn-amber:hover { background: rgba(245, 158, 11, 0.25); }
        .tv-btn-emerald {
          background: #059669;
          color: #ffffff;
          border-color: #10b981;
        }
        .tv-btn-active {
          background: #2563eb !important;
          color: #ffffff !important;
          border-color: #3b82f6 !important;
        }
        .tv-status-badge {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          padding: 3px 8px;
          border-radius: 8px;
          font-size: 11px;
          font-weight: 600;
          border: 1px solid #334155;
          background: #0f172a;
        }
        .tv-status-select {
          background: transparent;
          color: #e2e8f0;
          border: none;
          font-size: 11px;
          font-weight: 600;
          outline: none;
          cursor: pointer;
        }
        .tv-status-select option {
          background: #0f172a;
          color: #e2e8f0;
        }
        .tv-status-dot {
          width: 7px;
          height: 7px;
          border-radius: 50%;
          display: inline-block;
          background: #94a3b8;
        }
        .status-rascunho_ia .tv-status-dot { background: #94a3b8; }
        .status-em_revisao .tv-status-dot { background: #fbbf24; }
        .status-pronta_para_leitura .tv-status-dot { background: #a855f7; }
        .status-aprovada_com_ressalvas .tv-status-dot { background: #f97316; }
        .status-aprovada_definitiva .tv-status-dot { background: #10b981; }

        .tv-diff-del {
          background-color: #fee2e2;
          color: #991b1b;
          text-decoration: line-through;
          padding: 1px 3px;
          border-radius: 3px;
          margin: 0 1px;
        }
        .tv-diff-ins {
          background-color: #dcfce7;
          color: #166534;
          text-decoration: none;
          font-weight: 600;
          border-bottom: 2px solid #22c55e;
          padding: 1px 3px;
          border-radius: 3px;
          margin: 0 1px;
        }
        .tv-draft-banner {
          display: none;
          background: #1e1b4b;
          color: #c7d2fe;
          border: 1px solid #4338ca;
          border-radius: 8px;
          padding: 8px 14px;
          font-size: 12px;
          margin-bottom: 10px;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
        }
        .tv-draft-btn-group {
          display: flex;
          gap: 6px;
        }
        .tv-draft-btn {
          background: #4f46e5;
          color: #ffffff;
          border: none;
          border-radius: 6px;
          padding: 4px 10px;
          font-size: 11px;
          font-weight: 500;
          cursor: pointer;
        }
        .tv-draft-btn:hover { background: #4338ca; }
        .tv-draft-btn-cancel {
          background: #312e81;
          color: #e0e7ff;
        }
        .tv-draft-btn-cancel:hover { background: #1e1b4b; }
        .tv-autosave-badge {
          font-size: 11px;
          color: #64748b;
          margin-right: 4px;
          white-space: nowrap;
        }
        .tv-toast {
          display: none;
          background: #064e3b;
          color: #a7f3d0;
          border: 1px solid #059669;
          border-radius: 8px;
          padding: 8px 12px;
          font-size: 12px;
          margin-bottom: 10px;
        }
        .tv-paper {
          background: #ffffff;
          border: 1px solid #e2e8f0;
          border-radius: 16px;
          padding: 36px 44px;
          min-height: 460px;
          max-height: 760px;
          overflow-y: auto;
          box-shadow: 0 20px 25px -5px rgba(0,0,0,0.25);
        }
        .tv-empty {
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          text-align: center;
          padding: 80px 20px;
          color: #94a3b8;
        }
        .tv-hdr-wrap {
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 18px;
          border-bottom: 1px solid #e2e8f0;
          padding-bottom: 14px;
          margin-bottom: 16px;
        }
        .tv-logo {
          width: 72px;
          height: 72px;
          object-fit: contain;
          display: none;
        }
        .hdr-state { font-size: 11px; font-weight: 600; color: #475569; letter-spacing: 0.08em; }
        .hdr-org { font-size: 15px; font-weight: 700; color: #0f172a; margin-top: 2px; }
        .hdr-det { font-size: 11px; color: #64748b; margin-top: 2px; }
        .tv-title {
          text-align: center;
          font-size: 15px;
          font-weight: 700;
          text-transform: uppercase;
          margin: 14px 0 18px 0;
          padding: 4px;
        }
        .tv-body {
          font-family: 'Merriweather', Georgia, serif;
          font-size: 14.5px;
          line-height: 1.75;
          text-align: justify;
          color: #1e293b;
          white-space: pre-wrap;
          padding: 6px;
        }
        [contenteditable="true"]:focus {
          outline: 2px dashed #f59e0b;
          outline-offset: 4px;
          background: #fffbeb;
          border-radius: 4px;
        }
        .tv-mark {
          background: #fef08a;
          color: #854d0e;
          padding: 1px 4px;
          border-radius: 4px;
          border-bottom: 2px solid #eab308;
          cursor: help;
        }
        .tv-signatures {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 32px;
          text-align: center;
          margin-top: 48px;
          padding-top: 24px;
        }
        .tv-sig-line {
          border-top: 1px solid #475569;
          padding-top: 6px;
          font-size: 12px;
          font-weight: 600;
          color: #334155;
        }
        .tv-integrity-stamp {
          margin-top: 36px;
          padding-top: 14px;
          border-top: 1px dashed #cbd5e1;
          text-align: center;
          font-family: monospace;
        }
        .tv-stamp-title {
          font-size: 9px;
          font-weight: 700;
          color: #64748b;
          letter-spacing: 0.05em;
          margin-bottom: 2px;
        }
        .tv-stamp-hash {
          font-size: 8.5px;
          color: #94a3b8;
          word-break: break-all;
        }
        .tv-panels {
          display: none;
          grid-template-columns: 1fr 1fr;
          gap: 12px;
          margin-top: 12px;
        }
        @media (max-width: 768px) {
          .tv-panels { grid-template-columns: 1fr; }
          .tv-paper { padding: 20px; }
        }
        .tv-panel-card {
          background: #020617;
          border: 1px solid #1e293b;
          border-radius: 12px;
          padding: 12px 14px;
          color: #cbd5e1;
          font-size: 12px;
          max-height: 220px;
          overflow-y: auto;
        }
        .tv-panel-title {
          font-size: 11px;
          font-weight: 700;
          text-transform: uppercase;
          color: #f59e0b;
          margin-bottom: 8px;
          letter-spacing: 0.05em;
        }
        .tl-item {
          display: flex;
          align-items: flex-start;
          gap: 8px;
          padding: 4px 0;
          border-bottom: 1px solid #0f172a;
        }
        .tl-badge {
          font-family: monospace;
          font-size: 11px;
          color: #38bdf8;
          text-decoration: none;
          white-space: nowrap;
        }
        .tl-badge:hover { text-decoration: underline; }
      </style>

      <!-- Action Toolbar -->
      <div class="tv-toolbar">
        <div class="tv-btn-group">
          <button type="button" class="tv-btn" id="btn-validate" title="Converte números/horários para extenso, expande abreviações e garante parágrafo único">
            ✓ Normalizar Padrão Cartorial
          </button>
          <button type="button" class="tv-btn" id="btn-diff" title="Compara visualmente as edições feitas com a minuta original gerada pela IA">
            🔍 Ver Alterações (Diff)
          </button>
          <button type="button" class="tv-btn tv-btn-amber" id="btn-refine" title="Selecione uma frase no texto e clique para reescrever com IA">
            ✨ Reescrever Seleção com IA
          </button>
          <button type="button" class="tv-btn tv-btn-amber" id="btn-learn-name" title="Selecione um nome grafado errado no texto para corrigi-lo e ensiná-lo permanentemente à Biblioteca">
            👤 Ensinar Nome
          </button>
          <button type="button" class="tv-btn tv-btn-amber" id="btn-warnings" style="display:none;">
            ⚠️ Destacar Revisão
          </button>
        </div>
        <div class="tv-btn-group">
          <div class="tv-status-badge status-rascunho_ia" id="tv-status-badge" title="Ciclo de Vida Notarial da Ata">
            <span class="tv-status-dot"></span>
            <select id="tv-status-select" class="tv-status-select">
              <option value="rascunho_ia">🤖 Rascunho IA</option>
              <option value="em_revisao">📝 Em Revisão</option>
              <option value="pronta_para_leitura">📜 Pronta p/ Leitura</option>
              <option value="aprovada_com_ressalvas">⚠️ Aprovada c/ Ressalvas</option>
              <option value="aprovada_definitiva">✅ Aprovada Definitiva</option>
            </select>
          </div>
          <span id="tv-autosave-badge" class="tv-autosave-badge"></span>
          <span id="tv-stats" style="font-size:11px;color:#94a3b8;margin-right:4px;">0 palavras</span>
          ${showSave ? '<button type="button" class="tv-btn tv-btn-emerald" id="btn-save-host">💾 Salvar Ata</button>' : ''}
        </div>
      </div>

      <!-- Draft Recovery Banner -->
      <div id="tv-draft-banner" class="tv-draft-banner">
        <span id="tv-draft-info">⚠️ Há edições anteriores salvas localmente neste navegador. Deseja restaurá-las?</span>
        <div class="tv-draft-btn-group">
          <button type="button" class="tv-draft-btn" id="btn-draft-restore">Restaurar Rascunho</button>
          <button type="button" class="tv-draft-btn tv-draft-btn-cancel" id="btn-draft-discard">Descartar</button>
        </div>
      </div>

      <div id="tv-toast" class="tv-toast"></div>

      <!-- Paper Sheet -->
      <div class="tv-paper">
        <div id="tv-empty" class="tv-empty">
          <div style="font-size:15px;font-weight:600;color:#475569;">Nenhuma ata carregada no editor</div>
          <div style="font-size:12px;margin-top:4px;max-width:320px;">Gere uma ata pelo tVerbum ou chame <code>editor.setMinute(json)</code> no seu sistema.</div>
        </div>

        <div id="tv-sheet-content" style="display:none;">
          <div class="tv-hdr-wrap">
            <img id="tv-logo" class="tv-logo" alt="Brasão">
            <div id="tv-header" style="text-align:center;"></div>
          </div>

          <div id="tv-title" class="tv-title" contenteditable="true"></div>
          <div id="tv-body" class="tv-body" contenteditable="true"></div>

          <div id="tv-signatures" class="tv-signatures">
            <div><div class="tv-sig-line">Secretário</div></div>
            <div><div class="tv-sig-line">Presidente</div></div>
          </div>

          <div id="tv-integrity-stamp" class="tv-integrity-stamp" style="display:none;">
            <div class="tv-stamp-title">VALIDAÇÃO E INTEGRIDADE CARTORIAL (LEI Nº 14.063/2020)</div>
            <div id="tv-stamp-hash" class="tv-stamp-hash"></div>
          </div>
        </div>
      </div>

      <!-- Structured Metadata & Video Timeline Panels -->
      <div id="tv-Structured-panels" class="tv-panels">
        <div class="tv-panel-card">
          <div class="tv-panel-title">📊 Dados Extraídos para o Banco (JSON)</div>
          <div id="tv-extracted-list" style="line-height:1.6;"></div>
        </div>
        <div class="tv-panel-card">
          <div class="tv-panel-title">⏱️ Linha do Tempo do Vídeo (Clique para ouvir)</div>
          <div id="tv-timeline-list"></div>
        </div>
      </div>
    `;

    this.shadowRoot.getElementById('tv-body').addEventListener('input', () => {
      this._updateStats();
      this._scheduleAutosave();
    });
    this.shadowRoot.getElementById('tv-title').addEventListener('input', () => {
      this._updateStats();
      this._scheduleAutosave();
    });
    this.shadowRoot.getElementById('btn-validate').addEventListener('click', () => this.validateAndNormalize());
    this.shadowRoot.getElementById('btn-diff').addEventListener('click', () => this._toggleDiff());
    this.shadowRoot.getElementById('tv-status-select').addEventListener('change', (e) => this.setDocumentStatus(e.target.value));
    this.shadowRoot.getElementById('btn-draft-restore').addEventListener('click', () => this._restoreLocalDraft());
    this.shadowRoot.getElementById('btn-draft-discard').addEventListener('click', () => this._discardLocalDraft());
    this.shadowRoot.getElementById('btn-refine').addEventListener('click', () => {
      const instruction = prompt(
        'Como deseja reescrever o trecho selecionado?',
        'Tornar mais formal, impessoal e no padrão legislativo cartorial, mantendo números por extenso.'
      );
      if (instruction) this.refineSelectedSnippet(instruction);
    });
    this.shadowRoot.getElementById('btn-learn-name').addEventListener('click', () => this.quickLearnSelectedName());
    this.shadowRoot.getElementById('btn-warnings').addEventListener('click', () => this._toggleHighlights());

    const saveBtn = this.shadowRoot.getElementById('btn-save-host');
    if (saveBtn) {
      saveBtn.addEventListener('click', () => {
        this.dispatchEvent(new CustomEvent('tverbum-save', {
          detail: this.getMinute(),
          bubbles: true,
          composed: true,
        }));
      });
    }
  }
}

if (!customElements.get('tverbum-editor')) {
  customElements.define('tverbum-editor', TVerbumEditor);
}
