import datetime
import html
import json
import logging
import os
import tempfile
import uuid

import requests
from dotenv import load_dotenv
from telegram import File, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from urllib3.exceptions import InsecureRequestWarning

from db import (
    add_allowed,
    add_owner,
    get_owner,
    init_db,
    is_allowed,
    is_owner,
    list_allowed,
    list_owners,
    remove_allowed,
    remove_owner,
)

# Suppress SSL warnings for Railway environment
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
OWNER_TG_ID = int(os.getenv('OWNER_TG_ID', '0'))
DATAGRAB_KEY = os.getenv('DATAGRAB_KEY')
JSON_CACHE_LIMIT = 100

if not BOT_TOKEN or not OWNER_TG_ID or not DATAGRAB_KEY:
    print('Missing required env vars. See .env.example')
    raise SystemExit(1)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize DB (idempotent)
init_db(OWNER_TG_ID)


def is_effective_owner(user_id: int) -> bool:
    return user_id == OWNER_TG_ID or is_owner(user_id)


def remember_json_report(context: ContextTypes.DEFAULT_TYPE, report: dict) -> str:
    cache = context.bot_data.setdefault('json_reports', {})
    key = uuid.uuid4().hex[:16]
    cache[key] = report
    while len(cache) > JSON_CACHE_LIMIT:
        oldest_key = next(iter(cache))
        del cache[oldest_key]
    return key


def split_json_chunks(text: str, limit: int = 3200):
    lines = text.splitlines()
    chunks = []
    current = ''
    for line in lines:
        candidate = f'{current}\n{line}' if current else line
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = line
        else:
            chunks.append(line[:limit])
            rest = line[limit:]
            while rest:
                chunks.append(rest[:limit])
                rest = rest[limit:]
    if current:
        chunks.append(current)
    return chunks


def build_check_line(code: str, label: str, status: str, details: str) -> dict:
    return {
        'code': code,
        'label': label,
        'status': status,
        'details': details,
    }


def build_datagrab_report(data: dict, *, sender_id: int, file_name: str) -> dict:
    is_fake = bool(data.get('is_fake'))
    is_mod = bool(data.get('is_mod'))
    is_unrec = bool(data.get('is_unrec'))
    compliance = data.get('compliance_status')
    struct_result = data.get('struct_result')
    result_value = data.get('result')
    message = data.get('message')
    check_data = data.get('check_data') if isinstance(data.get('check_data'), dict) else {}

    checks = [
        build_check_line(
            'fake_check',
            'Признак подделки',
            'FAIL' if is_fake else 'OK',
            (
                'DataGrab вернул is_fake=true, поэтому чек помечен как поддельный'
                if is_fake else
                'DataGrab вернул is_fake=false, отдельный признак подделки не обнаружен'
            ),
        ),
        build_check_line(
            'modified_check',
            'Признак пересохранения',
            '50/50' if is_mod else 'OK',
            (
                'DataGrab вернул is_mod=true: документ был пересохранён или сформирован виртуальным принтером'
                if is_mod else
                'DataGrab вернул is_mod=false: следов пересохранения не найдено'
            ),
        ),
        build_check_line(
            'recognition_check',
            'Распознавание документа',
            '50/50' if is_unrec else 'OK',
            (
                'DataGrab вернул is_unrec=true: тип документа не распознан'
                if is_unrec else
                'DataGrab вернул is_unrec=false: документ распознан'
            ),
        ),
        build_check_line(
            'pdf_structure_check',
            'Структура PDF',
            'OK' if compliance is True else ('FAIL' if compliance is False and is_fake else '50/50' if compliance is False else 'INFO'),
            (
                'DataGrab вернул compliance_status=true: структура PDF корректна'
                if compliance is True else
                (
                    f'DataGrab вернул compliance_status=false: структура PDF нарушена; struct_result={struct_result or "null"}'
                    if compliance is False else
                    'DataGrab не вернул статус структуры PDF'
                )
            ),
        ),
    ]

    fail_count = sum(1 for item in checks if item['status'] == 'FAIL')
    warn_count = sum(1 for item in checks if item['status'] == '50/50')
    ok_count = sum(1 for item in checks if item['status'] == 'OK')

    if is_fake:
        verdict = 'FAIL'
        verdict_label = 'Чек поддельный'
    elif is_unrec or is_mod or compliance is False:
        verdict = 'REVIEW'
        verdict_label = 'Нужна ручная проверка'
    else:
        verdict = 'PASS'
        verdict_label = 'Чек выглядит оригинальным'

    if verdict == 'FAIL':
        verdict_reason = 'Вердикт FAIL построен по полям DataGrab, прежде всего is_fake=true и/или compliance_status=false.'
    elif verdict == 'REVIEW':
        verdict_reason = 'Вердикт REVIEW построен по полям DataGrab: найден спорный сигнал вроде is_mod=true, is_unrec=true или compliance_status=false.'
    else:
        verdict_reason = 'Вердикт PASS построен по полям DataGrab: критичных негативных сигналов в ответе API нет.'

    return {
        'summary': {
            'verdict': verdict,
            'verdict_label': verdict_label,
            'counts': {
                'OK': ok_count,
                'FAIL': fail_count,
                '50/50': warn_count,
            },
            'verdict_reason': verdict_reason,
            'decision_source': 'Вердикт сформирован локальной логикой бота на основе полей ответа DataGrab API.',
        },
        'context': {
            'sender_tg_id': sender_id,
            'file_name': file_name,
            'checked_at': datetime.datetime.now().isoformat(),
        },
        'api_overview': {
            'result': result_value,
            'message': message,
            'is_fake': is_fake,
            'is_mod': is_mod,
            'is_unrec': is_unrec,
            'compliance_status': compliance,
            'struct_result': struct_result,
            'paid_until': data.get('paid_until'),
            'last_checks': data.get('last_checks'),
        },
        'checks': checks,
        'check_data': check_data,
        'raw_api_response': data,
        'explanation': {
            'how_it_works': 'Бот не анализирует PDF самостоятельно. Он отправляет файл в DataGrab API и строит объяснение по полям ответа API.',
            'limitations': [
                'Если DataGrab не прислал подробное поле причины, бот не может восстановить внутреннюю механику проверки.',
                'Если struct_result=null, бот знает только факт нарушения структуры PDF, но не знает точный технический дефект.',
                'Если is_fake=true без расшифровки, бот знает итоговый флаг подделки, но не знает, какой именно внутренний эвристический сигнал его вызвал.',
            ],
            'important_fields': {
                'is_fake': data.get('is_fake'),
                'is_mod': data.get('is_mod'),
                'is_unrec': data.get('is_unrec'),
                'compliance_status': data.get('compliance_status'),
                'struct_result': data.get('struct_result'),
                'message': data.get('message'),
                'result': data.get('result'),
            },
        },
    }


def format_datagrab_response(data: dict) -> str:
    """Return a human-readable Russian summary for DataGrab API response."""
    lines = []
    result = data.get('result')

    if result:
        lines.append(f'📋 Результат: {result}')

    message = data.get('message', '')
    if message:
        lines.append(f'💬 {message}')

    is_fake = data.get('is_fake')
    is_mod = data.get('is_mod')
    is_unrec = data.get('is_unrec')
    compliance = data.get('compliance_status')

    lines.append('')
    if is_fake:
        lines.append('❌ ВЕРДИКТ: ЧЕК ПОДДЕЛЬНЫЙ')
        lines.append('Документ был изменен или пересоздан')
        lines.append('')
        lines.append('━━━━━━━━━━━━━━━━━━━━━━━━━━━')
        lines.append('⚠️ ОБНАРУЖЕННЫЕ НАРУШЕНИЯ:')
        lines.append('━━━━━━━━━━━━━━━━━━━━━━━━━━━')
        lines.append('❌ Чек не является оригиналом')
        lines.append('   └─ Подлинность не подтверждена')
        lines.append('   └─ Документ был изменен или пересоздан')

        if compliance is False:
            lines.append('❌ Структура PDF нарушена')
            lines.append('   └─ Файл не прошел проверку целостности')
            struct_result = data.get('struct_result')
            if struct_result:
                lines.append(f'   └─ Результат структуры: {struct_result}')

        if is_mod:
            lines.append('❌ Чек был пересохранён')
            lines.append('   └─ Сформирован виртуальным принтером')
            lines.append('   └─ Проверка надежности затруднена')

    elif is_unrec:
        lines.append('⚠️ ВЕРДИКТ: РАСПОЗНАВАНИЕ НЕ УДАЛОСЬ')
        lines.append('Чек не распознан системой')
        lines.append('')
        lines.append('━━━━━━━━━━━━━━━━━━━━━━━━━━━')
        lines.append('⚠️ ВОЗМОЖНЫЕ ПРОБЛЕМЫ:')
        lines.append('━━━━━━━━━━━━━━━━━━━━━━━━━━━')
        lines.append('⚠️ Чек не распознан (unrec)')
        lines.append('   └─ Система не смогла определить тип документа')
        lines.append('   └─ Возможно, чек от неподдерживаемого банка')
        lines.append('   └─ Или файл повреждён')

    else:
        lines.append('✅ ВЕРДИКТ: ЧЕК ОРИГИНАЛЬНЫЙ')
        if compliance is False:
            lines.append('⚠️ Структура PDF: Нарушена (но содержимое оригинальное)')
            struct_result = data.get('struct_result')
            if struct_result:
                lines.append(f'   └─ Результат структуры: {struct_result}')
        elif compliance is True:
            lines.append('✅ Структура PDF: Корректна')

    if is_mod and not is_fake:
        lines.append('⚠️ Внимание: Чек был пересохранён')
        lines.append('   └─ Сформирован виртуальным принтером')

    check_data = data.get('check_data', {})
    if isinstance(check_data, dict) and check_data:
        lines.append('')
        lines.append('━━━━━━━━━━━━━━━━━━━━━━━━━━━')
        lines.append('🧾 ДАННЫЕ ЧЕКА')
        lines.append('━━━━━━━━━━━━━━━━━━━━━━━━━━━')

        sender_name = check_data.get('sender_name')
        sender_acc = check_data.get('sender_acc')
        if sender_name or sender_acc:
            lines.append('📤 ОТПРАВИТЕЛЬ:')
            if sender_name:
                lines.append(f'   Имя: {sender_name}')
            if sender_acc:
                lines.append(f'   Счет: {sender_acc}')

        remitte_name = check_data.get('remitte_name')
        remitte_acc = check_data.get('remitte_acc')
        remitte_tel = check_data.get('remitte_tel')
        if remitte_name or remitte_acc or remitte_tel:
            lines.append('📥 ПОЛУЧАТЕЛЬ:')
            if remitte_name:
                lines.append(f'   Имя: {remitte_name}')
            if remitte_acc:
                lines.append(f'   Счет: {remitte_acc}')
            if remitte_tel:
                lines.append(f'   Телефон: {remitte_tel}')

        sum_val = check_data.get('sum')
        if sum_val:
            lines.append(f'💰 Сумма: {sum_val} ₽')

        status = check_data.get('status')
        if status:
            lines.append(f'✓ Статус: {status}')

        payment_time = check_data.get('payment_time')
        if payment_time:
            try:
                dt = datetime.datetime.fromtimestamp(payment_time)
                date_str = dt.strftime('%d.%m.%Y %H:%M:%S')
                lines.append(f'🕐 Дата платежа: {date_str}')
            except Exception:
                lines.append(f'🕐 Дата платежа (timestamp): {payment_time}')

        doc_id = check_data.get('doc_id')
        if doc_id:
            lines.append(f'📌 ID документа: {doc_id}')

    lines.append('')
    lines.append('━━━━━━━━━━━━━━━━━━━━━━━━━━━')

    paid_until = data.get('paid_until')
    if paid_until:
        lines.append(f'💳 Подписка активна до: {paid_until}')

    last_checks = data.get('last_checks')
    if last_checks is not None:
        lines.append(f'📊 Проверок ранее: {last_checks}')

    lines.append('')
    if is_fake:
        lines.append('⛔ РЕКОМЕНДАЦИЯ: ОТКЛОНИТЬ ЧЕК')
        lines.append('⛔ Чек является поддельным и не должен приниматься')
    elif is_unrec:
        lines.append('⚠️ РЕКОМЕНДАЦИЯ: ПРОВЕРИТЬ ВРУЧНУЮ')
        lines.append('⚠️ Чек не распознан, требуется ручная верификация')
    else:
        lines.append('✅ РЕКОМЕНДАЦИЯ: ЧЕК ПРИНЯТ')
        lines.append('✅ Чек пройдёт все проверки и может быть принят')

    return '\n'.join(lines)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        'Привет! Пришлите PDF чек как файл, я проверю его через DataGrab API.\n'
        'Owner может управлять доступом через /allow, /disallow, /list, /add_owner, /remove_owner, /owners'
    )


async def add_owner_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_effective_owner(user_id):
        await update.message.reply_text('Только owner может добавлять других owner.')
        return
    if not context.args:
        await update.message.reply_text('Использование: /add_owner <tg_id>')
        return
    try:
        tg_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text('tg_id должен быть числом.')
        return
    add_owner(tg_id)
    await update.message.reply_text(f'Owner {tg_id} добавлен.')


async def remove_owner_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_effective_owner(user_id):
        await update.message.reply_text('Только owner может удалять owner.')
        return
    if not context.args:
        await update.message.reply_text('Использование: /remove_owner <tg_id>')
        return
    try:
        tg_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text('tg_id должен быть числом.')
        return
    if not remove_owner(tg_id):
        await update.message.reply_text('Не удалось удалить owner. Нельзя оставить бота без owner.')
        return
    await update.message.reply_text(f'Owner {tg_id} удалён.')


async def owners_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_effective_owner(user_id):
        await update.message.reply_text('Только owner может просматривать список owner.')
        return
    owners = list_owners()
    if not owners:
        await update.message.reply_text('Список owner пуст.')
        return
    await update.message.reply_text('Owners:\n' + '\n'.join(str(owner) for owner in owners))


async def allow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_effective_owner(user_id):
        await update.message.reply_text('Только владелец может добавлять пользователей.')
        return
    if not context.args:
        await update.message.reply_text('Использование: /allow <tg_id>')
        return
    try:
        tg_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text('tg_id должен быть числом.')
        return
    add_allowed(tg_id)
    await update.message.reply_text(f'Пользователь {tg_id} добавлен в список allowed.')


async def disallow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_effective_owner(user_id):
        await update.message.reply_text('Только владелец может удалять пользователей.')
        return
    if not context.args:
        await update.message.reply_text('Использование: /disallow <tg_id>')
        return
    try:
        tg_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text('tg_id должен быть числом.')
        return
    remove_allowed(tg_id)
    await update.message.reply_text(f'Пользователь {tg_id} удален из allowed.')


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_effective_owner(user_id):
        await update.message.reply_text('Только владелец может просматривать список.')
        return
    users = list_allowed()
    if not users:
        await update.message.reply_text('Список allowed пуст.')
    else:
        await update.message.reply_text('Allowed users:\n' + '\n'.join(str(u) for u in users))


async def show_json_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ''
    key = data.split(':', 1)[1] if ':' in data else ''
    payload = context.bot_data.get('json_reports', {}).get(key)
    if not payload:
        await query.message.reply_text('JSON-отчёт не найден. Отправьте чек заново.')
        return

    json_text = json.dumps(payload, ensure_ascii=False, indent=2)
    chunks = split_json_chunks(json_text)
    for index, chunk in enumerate(chunks):
        prefix = '📦 Полный JSON отчёт\n\n' if index == 0 else ''
        await query.message.reply_text(
            f'{prefix}<pre>{html.escape(chunk)}</pre>',
            parse_mode='HTML',
        )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sender_id = update.effective_user.id
    if not is_effective_owner(sender_id) and not is_allowed(sender_id):
        await update.message.reply_text('Вам нет доступа. Обратитесь к владельцу бота.')
        return

    doc = update.message.document
    if not doc:
        await update.message.reply_text('Пожалуйста, отправьте PDF файл как документ.')
        return

    await update.message.reply_text('Получаю файл и отправляю на проверку...')
    file: File = await context.bot.get_file(doc.file_id)

    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tf:
        file_path = tf.name
        await file.download_to_drive(file_path)

    try:
        servers = [
            f'https://api.datagrab.ru/upload.php?key={DATAGRAB_KEY}&tid={sender_id}',
            f'https://api2.datagrab.ru/upload.php?key={DATAGRAB_KEY}&tid={sender_id}',
        ]

        data = None
        for idx, url in enumerate(servers):
            try:
                with open(file_path, 'rb') as f:
                    files = {'file': ('receipt.pdf', f, 'application/pdf')}
                    resp = requests.post(url, files=files, timeout=60, verify=False)
                resp.raise_for_status()
                try:
                    data = resp.json()
                    logger.info('Successfully got response from %s: %s', url, data)
                    break
                except ValueError:
                    raw_text = resp.text
                    logger.error('Non-JSON response from %s: %s', url, raw_text)
                    if idx == len(servers) - 1:
                        if len(raw_text) > 3900:
                            raw_text = raw_text[:3900] + '\n... (truncated)'
                        await update.message.reply_text(
                            f'DataGrab вернул не-JSON ответ:\n<pre>{html.escape(raw_text)}</pre>',
                            parse_mode='HTML',
                        )
                        return
            except Exception as exc:
                logger.warning('Error during request to %s: %s', url, exc)
                if idx == len(servers) - 1:
                    await update.message.reply_text(f'Ошибка при отправке на проверку: {exc}')
                    return

        if data is None:
            await update.message.reply_text('Не удалось получить ответ от DataGrab после попыток обоих серверов.')
            return

        report = build_datagrab_report(
            data,
            sender_id=sender_id,
            file_name=doc.file_name or 'receipt.pdf',
        )
        summary = format_datagrab_response(data)
        summary += (
            '\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
            f'📊 JSON-проверка: OK {report["summary"]["counts"]["OK"]} | '
            f'FAIL {report["summary"]["counts"]["FAIL"]} | '
            f'50/50 {report["summary"]["counts"]["50/50"]}\n'
            f'🧠 Итог: {report["summary"]["verdict_label"]}\n'
            f'📌 Основание: {report["summary"]["verdict_reason"]}\n'
            '🔬 Метод: бот не проверяет PDF сам, а объясняет ответ DataGrab API.\n'
            'Нажмите кнопку ниже, чтобы открыть полный JSON отчёт.'
        )
        cache_key = remember_json_report(context, report)
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton('📦 Полный JSON проверки', callback_data=f'json:{cache_key}')]]
        )
        await update.message.reply_text(summary, reply_markup=keyboard)

    except Exception as exc:
        logger.exception('Error during DataGrab API request')
        await update.message.reply_text(f'❌ Ошибка при проверке чека: {exc}')

    finally:
        try:
            os.remove(file_path)
        except Exception:
            pass


async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Неизвестная команда или сообщение. Отправьте PDF файл.')


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('allow', allow_cmd))
    app.add_handler(CommandHandler('disallow', disallow_cmd))
    app.add_handler(CommandHandler('list', list_cmd))
    app.add_handler(CommandHandler('add_owner', add_owner_cmd))
    app.add_handler(CommandHandler('remove_owner', remove_owner_cmd))
    app.add_handler(CommandHandler('owners', owners_cmd))
    app.add_handler(CallbackQueryHandler(show_json_callback, pattern=r'^json:'))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.ALL, unknown))

    print('Bot started...')
    app.run_polling()


if __name__ == '__main__':
    main()
