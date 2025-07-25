// static/wallet.js
import { apiCall } from './api.js';
import { escapeHtml } from './ui.js';

const tg = window.Telegram.WebApp;

export async function loadWallet() {
    const card = document.getElementById('walletInfoCard');
    const historyDiv = document.getElementById('walletHistory');
    card.innerHTML = `<p class="status-loading">Загрузка...</p>`;
    historyDiv.innerHTML = '';

    const data = await apiCall('/api/wallet');
    if (!data) {
        card.innerHTML = `<p class="status-error">Не удалось загрузить данные кошелька.</p>`;
        return;
    }
    
    card.innerHTML = `
        <h3>Кошелек</h3>
        <p><strong>Текущий баланс:</strong> ${escapeHtml(data.current_balance_rub_str)}</p>
        <button id="depositBtn">Пополнить баланс</button>
    `;
    card.classList.remove('status-loading');
    document.getElementById('depositBtn').addEventListener('click', () => {
        tg.sendData("/deposit_request_from_webapp");
    });
    
    if (data.transactions && data.transactions.length > 0) {
        const table = document.createElement('table');
        table.className = 'wallet-history-table';
        table.innerHTML = `<thead><tr><th>Дата</th><th>Операция</th><th>Сумма</th><th>Баланс</th></tr></thead>`;
        const tbody = document.createElement('tbody');
        data.transactions.forEach(tx => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td data-label="Дата">${escapeHtml(tx.created_at_display)}</td>
                <td data-label="Операция" title="${escapeHtml(tx.full_description)}">${escapeHtml(tx.description)}</td>
                <td data-label="Сумма" class="amount ${tx.amount_rub_str.startsWith('+') ? 'amount-plus' : 'amount-minus'}">${escapeHtml(tx.amount_rub_str)}</td>
                <td data-label="Баланс">${escapeHtml(tx.balance_after_rub_str)}</td>
            `;
            tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        historyDiv.appendChild(table);
    } else {
        historyDiv.innerHTML = `<p>История операций пуста.</p>`;
    }
}