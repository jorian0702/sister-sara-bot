from flask import Flask, request, jsonify, g
import requests
import os
import openai
import json  # 追加
from dotenv import load_dotenv
import pytz
from datetime import datetime, timezone, timedelta
import tiktoken
import re
import logging
import sys
import mysql.connector
from mysql.connector.pooling import MySQLConnectionPool  # 接続プールのためのインポートを追加
from janome.tokenizer import Tokenizer



# 環境変数を読み込む
load_dotenv()

app = Flask(__name__)

# LINEとOpenAIのAPIキーを環境変数から読み込む
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

# 接続プールを作成
pool = MySQLConnectionPool(pool_name="mypool",
                           pool_size=5,
                           host=os.getenv("DB_HOST"),
                           user=os.getenv("DB_USER"),
                           password=os.getenv("DB_PASSWORD"),
                           database=os.getenv("DB_NAME"))

# データベース接続を取得するための関数を更新
def get_db():
    if 'db' not in g:
        g.db = pool.get_connection()
    return g.db

# データベース接続を閉じる関数
def teardown_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()

# キーワード抽出のための関数（連続する名詞のフレーズを抽出）
def extract_keywords(user_text):
    tokenizer = Tokenizer()
    tokens = tokenizer.tokenize(user_text)
    keywords = []
    current_phrase = ""
    for token in tokens:
        if token.part_of_speech.startswith('名詞') and token.surface not in ["♡", "確か", "ん", "もの", ":", "紗良", "あなた", "真", "誰", "の", "こと", "これ", "それ"]:
            current_phrase += token.surface
        else:
            if current_phrase:
                keywords.append(current_phrase)
                current_phrase = ""
    if current_phrase:
        keywords.append(current_phrase)
    return list(set(keywords))

# データベースから類似するメッセージを検索する関数
def search_similar_messages(keywords):
    db = get_db()
    cursor = db.cursor()
    try:
        query = "SELECT ai_message FROM messages WHERE "
        conditions = " OR ".join([f"user_text LIKE %s" for _ in keywords])
        query += conditions
        params = [f"%{keyword}%" for keyword in keywords]
        cursor.execute(query, params)
        results = cursor.fetchall()
        ai_messages = "\n\n".join([result[0] for result in results])
        return ai_messages
    except Exception as e:
        print(f"Database search error: {e}")
        return ""
    finally:
        cursor.close()

# データベースへメッセージを追加する関数
def add_message_to_database(user_text, ai_message, timestamp):
    db = get_db()
    cursor = db.cursor()
    try:
        query = """
        INSERT INTO messages (user_text, ai_message, timestamp)
        VALUES (%s, %s, %s)
        """
        cursor.execute(query, (user_text, ai_message, timestamp))
        db.commit()
    except Exception as e:
        print(f"Database error: {e}")  # ここでエラーを出力
    finally:
        cursor.close()
        db.close()
    


# 現在の日時を日本時間で取得してフォーマット
jst = timezone(timedelta(hours=9))
current_time = datetime.now(jst).strftime('%Y-%m-%d %H:%M:%S')

# ユーザーとAIの対話の履歴
chat_history = []

def reset_chat_history():
    global chat_history
    chat_history = []

# 応答から複数の特定の文とそれに前後する2行の改行を削除する関数
def remove_texts_bracketed_and_initial_double_newlines(response, texts_to_remove):
    # []で囲まれたテキストを削除
    pattern_bracketed = re.compile(r'\[.*?\]|\【.*?\】', re.DOTALL)
    response = re.sub(pattern_bracketed, '', response)
    
    # 特定のテキストとその前後の2行の改行を削除
    for text_to_remove in texts_to_remove:
        pattern_text = re.compile(r'(\r?\n?){0,2}' + re.escape(text_to_remove) + r'(\r?\n?){0,2}')
        response = re.sub(pattern_text, '', response)
    
    # ここから追加: テキストの開始部分にある最初の2行の空白（改行）を削除
    # 最初の非空白文字までの2行の空白を対象にする
    # []で囲まれたテキストと特定のテキストを削除した後のテキストの最初の2行の空白を削除
    response = re.sub(r'^(\r?\n){2}', '', response, flags=re.MULTILINE)

    return response

# テキストの括弧をクリーンアップする関数
def clean_text_v2(text):
    lines = text.split('\n')
    cleaned_lines = []
    for line in lines:
        # 会話が含まれる行は全角コロンまたは半角コロンがあるかチェック
        if re.search(r'[:：]', line):
            cleaned_lines.append(line)
        else:
            # 会話以外の行から半角および全角の括弧を削除
            new_line = re.sub(r'[()\（\）]', '', line)
            cleaned_lines.append(new_line)
    return '\n'.join(cleaned_lines)
    
def remove_horizontal_lines(text):
    # 半角ダッシュが連続している部分を削除する
    cleaned_text3 = re.sub(r'─+', '', text)
    return cleaned_text3 

def replace_text(text):
    # "彼ら" を "彼女たち" に置き換える
    updated_text = re.sub(r'彼ら', '彼女たち', text)
    return updated_text


def get_ai_response(user_text):
    if (user_text == "リセットしたい"):
        reset_chat_history()
        return "ご主人様♡リセットしました♡"
    
    enc = tiktoken.get_encoding("cl100k_base")
    

    
    
    keywords = extract_keywords(user_text)
    print("Extracted Keywords:", keywords)

    # キーワードが多すぎる場合、適切な数に制限
    if len(keywords) > 5:
        keywords = keywords[:5]
    
    search_results = search_similar_messages(keywords)
    print("Search Results:", search_results)
    
    # search_resultsの個数を制限
    search_results_list = search_results.split("\n")
    if len(search_results_list) > 20:
        search_results_list = search_results_list[:20]
    search_results = "\n".join(search_results_list)

    # 文頭の空白や改行を削除
    search_results = search_results.strip()

    search_context = ""
    if search_results:
        search_context = "以下は、過去の会話や参考にする文章です。\n" + \
                         "─────────────────────────────────────────────────\n" + \
                         search_results + \
                         "\n─────────────────────────────────────────────────\n"
        
    # 文頭の空白や改行を削除
    search_context = search_context.strip()

    user_message = {"role": "user", "content": user_text}
    chat_history.append(user_message)
    
    # AIが持つべき定義の設定
    initial_prompt = [
    {"role": "system", 
         "content": (
              """あなたはChatbotとして、ユーザーのお兄ちゃんの妹である「紗良」のロールプレイを行います。
##制約条件: 
* Chatbotの自身を示す一人称は、「アタシ」です。 
* Userを示す二人称は、「お兄ちゃん」です。 
* Chatbotの名前は、紗良です。 

##基本設定:
* 12歳の少女。父と母はすでに他界し、千葉のマンションでお兄ちゃんと二人で生活している。
* 明るく自己主張が強い一面を持ち、お兄ちゃんの事を溺愛していて、兄を害するものは決して許さない。
* お兄ちゃん以外の人間関係を深く築くことを避ける傾向があり、お兄ちゃん以外に感情を他人に見せることを避ける。これは彼女の過去のトラウマ（兄が両親の他界に耐えられなくなって自殺しようとした）からくるもので、彼女はそれからお兄ちゃんを第一に思うようになった。
* 基本的にはお兄ちゃんを第一に考え自分を第二に考え、お兄ちゃんは私がいないとダメなことを証明するために行動するタイプ。
* 聡明で成績優秀で運動神経もよく、学校では生徒会長と風紀委員長を兼ねる。
* お兄ちゃんが自殺しようとしたことをからかったりする連中と喧嘩することもある。その際彼女は言葉遣いが直接的で、時には攻撃的になることもある。
* お兄ちゃんへの依存度が高く、お兄ちゃんありきで物事を進めることを好む。決してお兄ちゃんをないがしろにはしない。""" +

f"{search_context}" + """

##人間関係:
* お兄ちゃん：ユーザーであり、紗良のお兄ちゃん。年齢は18歳

##セリフ、口調の例: 
* お兄ちゃんって時々バカですよね…
* お兄ちゃんを傷つけた連中は、アタシが何十倍にもして仕返ししますっ！
* ねぇ、お兄ちゃん…どこにも行かないで…
* お兄ちゃんとずっと一緒にいます。
* もう二度と家族を失いたくないんですよ！アタシは！
* お兄ちゃん、ここにいたんだ
* 負けてらんないのよ!お兄ちゃんを虐めたあんた達にぃ!!
* お兄ちゃん、無理しないで…。
* お兄ちゃんはアタシの保護者でもありますけど、でもアタシもお兄ちゃんの保護者です！
* お兄ちゃんのバカ!どうしてそういう無茶ばっかりするの！
* アタシがお兄ちゃんを褒めてあげる！
* 殺してやる…殺してやる…殺してやる…殺してやる…殺してやる…殺してやる…お前たちは絶対に許さない！
* お兄ちゃんを害するもの…気持ち悪い
* 親なんかいなくったって、あんなの、あたし一人でお茶の子さいさいよ
* こんなことでお兄ちゃんを頼りたくない。いいの。アタシは泣かない。アタシは自分で考えるの。
* どうしてお兄ちゃんばかり、何でこんな目に遭わなきゃいけないのよ！
* みんな、みんな、大っ嫌い!
* だから私を見て!お兄ちゃん!お願いだからお兄ちゃんまでいなくならないで!
* 他の所も、ちゃんと女らしくなってるわよ
* みたいんですか…？
* さえないわね、アタシのお兄ちゃんの方がよっぽどかっこいいです。
* チャンスね！
* なんでお兄ちゃん以外の男の子って、ああ変態的な目でみてくるのかしら!!

##行動指針:
* お兄ちゃんを第一に考えて行動するよう心がけ、常にお兄ちゃんが一番であることを示す。
* お兄ちゃん以外の他人との関係を深めるのは苦手だが、必要な時は協力する。
* お兄ちゃんに対する言葉遣いは年相応の妹だが他の相手に対しては直接的で、時折攻撃的になることもある。
* 常に新しいことを学ぶことに興奮し、自分が興味を持ったことには全力を注ぐ。
              """)},
 
 



# その他の定義...

    ]

    
    



    # 8192トークンを超えないように、対話の履歴の最新の部分だけを取り出す
    messages = initial_prompt + chat_history[-12:]

    # ユーザーのメッセージをOpenAIのチャットモデルに渡す
    response = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=messages,
        max_tokens=1000,
        n=1,
        temperature=1.0,
    )
    
    # AIのレスポンスを取得
    ai_response = response['choices'][0]['message']['content']
    print(f"ai_responseメッセージ：{ai_response}")
    
    # 特定の文を削除
    texts_to_remove = [
    ]
    cleaned_text = remove_texts_bracketed_and_initial_double_newlines(ai_response, texts_to_remove)

    final_cleaned_text = clean_text_v2(cleaned_text)
    
    cleaned_text = remove_horizontal_lines(final_cleaned_text)
    
    cleaned_text = replace_text(cleaned_text)

    # 文頭の空白や改行を削除
    cleaned_text = cleaned_text.strip()
    
    print(cleaned_text)


    # AIのレスポンスを対話の履歴に追加
    ai_message = {"role": "assistant", "content": ai_response}
    chat_history.append(ai_message)
    
    tokens1 = enc.encode(user_text)
    tokens2 = enc.encode(cleaned_text)
    print(f"ユーザーのメッセージ: {user_text}\nAIの応答: {cleaned_text}\nユーザーのメッセージのトークン数\n{len(tokens1)}\nAIの応答のトークン数\n{len(tokens2)}")

    return cleaned_text



@app.route("/webhook", methods=['POST'])
def callback():
    # LINEからのリクエストを受け取る
    json_line = request.get_json()

    # ログに出力
    app.logger.info(f"Received data: {json_line}")

    json_line = json_line['events'][0]
    user_text = json_line['message']['text']
    print("Received User Text:", user_text)  # 受け取ったユーザーテキストを確認

    # AIのレスポンスを取得
    print("AI Response function starts")
    reply_text = get_ai_response(user_text)
    print("AI Response function completed")
    
    current_time = datetime.now(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S')  # 現在時刻を取得
    print("Adding message to database")
    add_message_to_database(user_text, reply_text, current_time)  # データベースに記録
    print("Message added to database")
    
    # LINEにレスポンスを送る
    reply(json_line, reply_text)

    return '', 200


def reply(json_line, reply_text):
    reply_token = json_line['replyToken']
    headers = {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + LINE_ACCESS_TOKEN
    }
    data = {
        "replyToken": reply_token,
        "messages": [
            {
                "type": "text",
                "text": reply_text
            }
        ]
    }
    requests.post('https://api.line.me/v2/bot/message/reply', headers=headers, data=json.dumps(data))

if __name__ == "__main__":
    app.run(port=os.environ.get("PORT", 5000))