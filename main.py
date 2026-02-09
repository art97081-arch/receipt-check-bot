import os
import logging
import tempfile
import requests
from dotenv import load_dotenv
from telegram import Update, File
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from db import init_db, add_allowed, remove_allowed, list_allowed, is_allowed, get_owner

load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
OWNER_TG_ID = int(os.getenv('OWNER_TG_ID', '0'))
DATAGRAB_KEY = os.getenv('DATAGRAB_KEY')

if not BOT_TOKEN or not OWNER_TG_ID or not DATAGRAB_KEY:
    print('Missing required env vars. See .env.example')
    exit(1)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize DB (idempotent)
init_db(OWNER_TG_ID)


def format_datagrab_response(data: dict) -> str:
    """Return a human-readable Russian summary for DataGrab API response."""
    lines = []
    result = data.get('result')

    # High-level result
    if result in (None, ''):
        lines.append('Результат: неизвестен')
    else:
        lines.append(f'Результат: {result}')

    # message from API (friendly)
    if data.get('message'):
        lines.append(data.get('message'))

    # Flags
    is_fake = data.get('is_fake')
    is_mod = data.get('is_mod')
    is_unrec = data.get('is_unrec')
    compliance = data.get('compliance_status')

    # Interpret authenticity
    if is_fake:
        lines.append('\n❌ Оригинальность: Не подтверждена')
        lines.append('❌ Чек не является оригиналом — документ был изменен или пересоздан')
    elif is_unrec:
        lines.append('\n⚠️ Распознавание: Не удалось распознать чек (unrec)')
    else:
        lines.append('\n✅ Оригинальность: Подтверждена')

    # Structure / compliance
    if compliance is False:
        lines.append('❌ Структура PDF: Нарушена')
    elif compliance is True:
        lines.append('✅ Структура PDF: Корректна')

    if is_mod:
        lines.append('⚠️ Чек был пересохранён/сформирован виртуальным принтером (mod) — проверка ограничена')

    # If check_data exists, print main fields
    check_data = data.get('check_data')
    if isinstance(check_data, dict):
        lines.append('\n🧾 Данные чека:')
        sender = check_data.get('sender_name') or check_data.get('sender') or check_data.get('sender_acc')
        if sender:
            lines.append(f'  Отправитель: {sender}')
        sender_bank = check_data.get('sender_bank') or check_data.get('bank_sender')
        if sender_bank:
            lines.append(f'  Банк отправителя: {sender_bank}')
        remitte = check_data.get('remitte_name') or check_data.get('recipient')
        if remitte:
            lines.append(f'  Получатель: {remitte}')
        remitte_bank = check_data.get('remitte_bank') or check_data.get('bank_recipient')
        if remitte_bank:
            lines.append(f'  Банк получателя: {remitte_bank}')
        amount = check_data.get('amount') or check_data.get('sum') or check_data.get('payment_sum')
        if amount:
            lines.append(f'  Сумма: {amount}')
        status = check_data.get('status') or check_data.get('state')
        if status:
            lines.append(f'  Статус: {status}')
        time = check_data.get('payment_time') or check_data.get('time')
        if time:
            lines.append(f'  Дата: {time}')

    # Additional recommendation based on flags
    lines.append('')
    if is_fake:
        lines.append('Чек поддельный — рекомендуется отклонить.')
    elif is_unrec or (not compliance and compliance is not None):
        lines.append('Рекомендация: Проверить вручную — частично распознан или структура нарушена.')
    else:
        lines.append('Рекомендация: Чек можно принять — все проверки пройдены.')

    return '\n'.join(lines)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Пришлите PDF чек как файл, я проверю его через DataGrab API.\n" 
        "Владелец бота может добавлять пользователей через /allow <tg_id>"
    )

async def allow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != OWNER_TG_ID:
        await update.message.reply_text('Только владелец может добавлять пользователей.')
        return
    args = context.args
    if not args:
        await update.message.reply_text('Использование: /allow <tg_id>')
        return
    try:
        tg_id = int(args[0])
    except ValueError:
        await update.message.reply_text('tg_id должен быть числом.')
        return
    add_allowed(tg_id)
    await update.message.reply_text(f'Пользователь {tg_id} добавлен в список allowed.')

async def disallow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != OWNER_TG_ID:
        await update.message.reply_text('Только владелец может удалять пользователей.')
        return
    args = context.args
    if not args:
        await update.message.reply_text('Использование: /disallow <tg_id>')
        return
    try:
        tg_id = int(args[0])
    except ValueError:
        await update.message.reply_text('tg_id должен быть числом.')
        return
    remove_allowed(tg_id)
    await update.message.reply_text(f'Пользователь {tg_id} удален из allowed.')

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != OWNER_TG_ID:
        await update.message.reply_text('Только владелец может просматривать список.')
        return
    users = list_allowed()
    if not users:
        await update.message.reply_text('Список пуст.')
    else:
        await update.message.reply_text('Allowed users:\n' + '\n'.join(str(u) for u in users))

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sender_id = update.effective_user.id
    if sender_id != OWNER_TG_ID and not is_allowed(sender_id):
        await update.message.reply_text('Вам нет доступа. Обратитесь к владельцу бота.')
        return

    doc = update.message.document
    if not doc:
        await update.message.reply_text('Пожалуйста, отправьте PDF файл как документ.')
        return
    
    # Download file
    await update.message.reply_text('Получаю файл и отправляю на проверку...')
    file: File = await context.bot.get_file(doc.file_id)
    
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tf:
        file_path = tf.name
        await file.download_to_drive(file_path)
        
        try:
            # Try primary and backup DataGrab servers
            servers = [
                f'https://api.datagrab.ru/upload.php?key={DATAGRAB_KEY}&tid={sender_id}',
                f'https://api2.datagrab.ru/upload.php?key={DATAGRAB_KEY}&tid={sender_id}'
            ]
            
            data = None
            for idx, url in enumerate(servers):
                try:
                    with open(file_path, 'rb') as f:
                        files = {'file': ('receipt.pdf', f, 'application/pdf')}
                        resp = requests.post(url, files=files, timeout=60)
                    resp.raise_for_status()
                    # Try parsing JSON; if parsing fails, send the raw response back to the user for inspection
                    try:
                        data = resp.json()
                        logger.info(f'Successfully got response from {url}: {data}')
                        break  # Success, exit loop
                    except ValueError as ve:
                        raw_text = resp.text
                        logger.error(f'Non-JSON response from {url}: {raw_text}')
                        if idx == len(servers) - 1:  # Last server, send error to user
                            if len(raw_text) > 3900:
                                raw_text = raw_text[:3900] + '\n... (truncated)'
                            await update.message.reply_text(f'DataGrab вернул не-JSON ответ:\n<pre>{raw_text}</pre>', parse_mode='HTML')
                            return
                except Exception as e:
                    logger.warning(f'Error during request to {url}: {e}')
                    if idx == len(servers) - 1:  # Last server, send error to user
                        await update.message.reply_text(f'Ошибка при отправке на проверку: {e}')
                        return
            
            if data is None:
                await update.message.reply_text('Не удалось получить ответ от DataGrab после попыток обоих серверов.')
                return
            
            # Format a human-friendly summary and send + raw JSON
            summary = format_datagrab_response(data)
            await update.message.reply_text(summary)

            # send raw JSON (trim if large)
            import json
            raw = json.dumps(data, ensure_ascii=False, indent=2)
            if len(raw) > 3900:
                raw = raw[:3900] + '\n... (truncated)'
            await update.message.reply_text(f'Полный ответ:\n<pre>{raw}</pre>', parse_mode='HTML')
        
        except Exception as e:
            logger.exception('Error during DataGrab API request')
            await update.message.reply_text(f'❌ Ошибка при проверке чека: {str(e)}')
        
        finally:
            # Clean up temp file
            try:
                os.remove(file_path)
            except:
                pass

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Неизвестная команда или сообщение. Отправьте PDF файл.')

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('allow', allow_cmd))
    app.add_handler(CommandHandler('disallow', disallow_cmd))
    app.add_handler(CommandHandler('list', list_cmd))

    # document handler
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    # fallback
    app.add_handler(MessageHandler(filters.ALL, unknown))

    print('Bot started...')
    app.run_polling()

if __name__ == '__main__':
    main()
