from io import BytesIO
import datetime
import logging

import requests
import urllib3
import configparser
import huspacy
import pdfplumber
from jinja2 import Environment, FileSystemLoader, select_autoescape
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs
from bmmbackend import bmmbackend
import bmmtools
from bmm_kozlonydb import Bmm_KozlonyDB

def download_data(year, month):

    url = config['Download']['url']
    
    pagenum = 0
    pagecount = 0

    while True:
        pagenum = pagenum + 1

        params = {
            'year': year,
            'month' : month,
            'serial' : '',
            'page' : pagenum
        }

        page = requests.get(url, params = params, verify = False)
        logging.info(page.url)
        soupage = BeautifulSoup(page.content, 'html.parser')

        entry = {}
        journalrows = soupage.find_all('div', class_ = 'journal-row')
        for journalrow in journalrows:

            docurl = journalrow.find('meta', {'itemprop': 'url'})['content']

            logging.info(docurl)

            dochash = urlparse(docurl).path.split('/')[-2]
            if db.getDoc(dochash) is None:
                entry['scrapedate'] = datetime.datetime.now()
                entry['issuedate'] = journalrow.find('meta', {'itemprop': 'datePublished'})['content']

                logging.info(f"New: {entry['issuedate']}")

                entry['url'] = docurl
                
                anchors = journalrow.find_all('a')
                for anchor in anchors:
                    if 'hivatalos-lapok' in anchor['href'] and 'dokumentumok' in anchor['href'] and anchor.find('b', {'itemprop': 'name'}):
                        entry['pdfurl'] = anchor['href']
                        entry['title'] = anchor.find('b', {'itemprop': 'name'}).decode_contents()

                res = requests.get(entry['pdfurl'], verify = False).content
                with pdfplumber.open(BytesIO(res)) as pdf:
                    entry['content'] = ''
                    entry['lemmacontent'] = ''
                    texts = []
                    pdfpagenum = 0
                    for page in pdf.pages:
                        texts.append(page.extract_text())
                        pdfpagenum = pdfpagenum + 1
                        if pdfpagenum == 10:
                            lemmas = []
                            if config['DEFAULT']['donotlemmatize'] == '0':
                                lemmas = bmmtools.lemmatize(nlp, texts)
                            entry['lemmacontent'] = entry['lemmacontent'] + " ".join(lemmas)
                            entry['content'] = entry['content'] + "\n".join(texts)
                            pdfpagenum = 0
                            texts = []

                    lemmas = []
                    if config['DEFAULT']['donotlemmatize'] == '0':
                        lemmas = bmmtools.lemmatize(nlp, texts)
                    entry['lemmacontent'] = entry['lemmacontent'] + " ".join(lemmas)
                    entry['content'] = entry['content'] + "\n".join(texts)

                    db.saveDoc(dochash, entry)
                    db.commitConnection()


        # getting page count
        if pagecount == 0:
            pagination = soupage.find('ul', class_ = 'pagination')
            if pagination:
                href = pagination.find_all('li')[-2].find('a')['href']
                query_params = parse_qs(urlparse(href).query)
                pagecount = int(query_params.get("page", [0])[0])

        if pagenum >= pagecount:
            break


def clearIsNew(ids):
    
    for num in ids:
        logging.info(f"Clear isnew: {num}")
        db.clearIsNew(num)

    db.commitConnection()


# some certificate problems
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

config = configparser.ConfigParser()
config.read_file(open('config.ini'))
logging.basicConfig(
    filename=config['DEFAULT']['logfile_name'], 
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s | %(module)s.%(funcName)s line %(lineno)d: %(message)s')

logging.info('KozlonyScraper started')

db = Bmm_KozlonyDB(config['DEFAULT']['database_name'])
backend = bmmbackend(config['DEFAULT']['monitor_url'], config['DEFAULT']['uuid'])

foundIds = []

env = Environment(
    loader=FileSystemLoader('templates'),
    autoescape=select_autoescape()
)
contenttpl = env.get_template('content.html')

if config['DEFAULT']['donotlemmatize'] == '0':
    nlp = huspacy.load()

lastissuedate = db.getLastIssueDate()
if (lastissuedate):
    d = datetime.datetime.strptime(lastissuedate, '%Y-%m-%d')
else:
    d = datetime.datetime.now()

download_data(year = d.year, month = d.month)

# ha d nem az aktualis honap, akkor az aktualis honapra is kell futtatni download_data-t
ma = datetime.datetime.now()
if d.year != ma.year or d.month != ma.month:
    download_data(year = ma.year, month = ma.month)

events = backend.getEvents()
for event in events['data']:
    result = None

    try:
        if event['type'] == 1:
            keresoszo = bmmtools.searchstringtofts(event['parameters'])
            if keresoszo:
                result = db.searchRecords(keresoszo)
                for res in result:
                    foundIds.append(res[0])
        else:
            result = db.getAllNew()
            for res in result:
                foundIds.append(res[0])

        if result:
            content = ''
            for res in result:
                content = content + contenttpl.render(doc = res)

            if config['DEFAULT']['donotnotify'] == '0':
                backend.notifyEvent(event['id'], content)
                logging.info(f"Notified: {event['id']} - {event['type']} - {event['parameters']}")
    except Exception as e:
        logging.error(f"Error: {e}")
        logging.error(f"Event: {event['id']} - {event['type']} - {event['parameters']}")


logging.info('foundIds: ')
logging.info(foundIds);

if config['DEFAULT']['staging'] == '0':
    clearIsNew(foundIds)

db.closeConnection()

logging.info('KozlonyScraper ready. Bye.')

print('Ready. Bye.')
