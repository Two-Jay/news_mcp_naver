import os

anthropic_api_key = os.environ.get('ANTHROPIC_API_KEY')
naver_client_id = os.environ.get('NAVER_CLIENT_ID')
naver_client_secret = os.environ.get('NAVER_CLIENT_SECRET')

os.environ['ANTHROPIC_API_KEY'] = anthropic_api_key
os.environ['NAVER_CLIENT_ID'] = naver_client_id
os.environ['NAVER_CLIENT_SECRET'] = naver_client_secret