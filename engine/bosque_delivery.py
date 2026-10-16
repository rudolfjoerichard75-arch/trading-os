"""Telegram acknowledgement handling. Never retry an ambiguous send blindly."""
import requests


def deliver(message, token, chat_id):
    if not token or not chat_id:
        return {'status': 'FAILED', 'reason': 'TELEGRAM_NOT_CONFIGURED'}
    try:
        response = requests.post('https://api.telegram.org/bot'+token+'/sendMessage',
                                 json={'chat_id':chat_id, 'text':message}, timeout=20)
        try: body = response.json()
        except (ValueError, TypeError): body = None
        if isinstance(body,dict) and body.get('ok') is True:
            result = body.get('result')
            if response.ok and isinstance(result,dict) and isinstance(result.get('message_id'),int):
                return {'status': 'SENT', 'message_id': result['message_id'],
                        'telegram_date': result.get('date')}
        # A documented API rejection is safe to retry on a later closed bar.
        if isinstance(body,dict) and body.get('ok') is False and response.status_code < 500:
            return {'status':'FAILED','reason':'TELEGRAM_REJECTED','http_status':response.status_code}
        return {'status':'DELIVERY_UNKNOWN','reason':'ACK_NOT_CONFIRMED'}
    except requests.RequestException:
        # A timeout/reset can occur after Telegram accepted a message.
        return {'status':'DELIVERY_UNKNOWN','reason':'NETWORK_OUTCOME_UNKNOWN'}
