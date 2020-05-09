'''
Handles financial logic
'''
import re
from bs4 import BeautifulSoup
from json import JSONEncoder
from datetime import datetime
import pandas as pd
from word2number import w2n


class FinancialReportEncoder(JSONEncoder):

    def default(self, o):
        if isinstance(o, datetime):
            return o.isoformat()
        return o.__dict__


class FinancialElement:
    '''
    Models financial elements
    '''

    def __init__(self, label, value):
        self.label = label
        self.value = value

    def __repr__(self):
        return str(self.__dict__)


class FinancialInfo:
    '''
    Models financial data provided in a financial report
    financial elements are stored in a map to retain flexibility
    '''

    def __init__(self, date, months, map):
        '''
        :param date: date of the information
        :param months: number of months that it covers (None if balance sheet)
        :param map: map of XBRL element name to value
        '''
        self.date = date
        self.months = months
        self.map = map

    def __repr__(self):
        return str(self.__dict__)


class FinancialReport:
    '''
    Models financial reports from an edgar filing
    financial elements are stored in a map to retain flexibility
    '''

    def __init__(self, company, date_filed, reports=[]):
        '''
        :param company: identifier for a company (not using the term "symbol"
            because not all companies that file on edgar are publicly traded)
        :param reports: list of FinancialInfo objects
        '''
        self.company = company
        self.date_filed = date_filed
        self.reports = reports

    def add_financial_info(self, financial_info: FinancialInfo):
        self.reports.append(financial_info)

    def __repr__(self):
        return str(self.__dict__)


class MetaDataParsingException(Exception):
    pass


# https://pypi.org/project/python-xbrl/

'''
XBRL rules for us-gaap namespace are found at the site below
https://xbrl.us/data-rule/dqc_0015-le/

spreadsheet is in docs folder

Notes:
 - only accept us-gaap based filings

For 10-K or 10-Q
1. get 10-K/10-Q filing from filings list
2. for each filing, in the filing text doc, find the FilingSummary.xml
3. In FilingSummary.MyReports, find the Reports with ShortNames matching
   what's set in STATEMENT_SHORT_NAMES (lower case)
4. get the HtmlFileName of the Report
5. find the DOCUMENT with the given FILENAME in HtmlFileName
The next part differs based on 10-K and 10-Q
6. in the TEXT.html.body, get the data in the first table (class="report") and 
   parse. 
   a. Exclude the first row (title and 12 Months Ended text)
   b. Should have four columns, with the last three representing the 
      current year, last year, and two years ago (order may vary).
   c. Years will be in th elements (class="th"), data in the td elements with
      class="nump"
   d. The first td in each row will tell us the us-gaap namespace elementName.
      This will be in the onclick of the a tag in the td, e.g.
        onclick="top.Show.showAR( this, 'defref_us-gaap_CostOfGoodsSold'...
      Some might not have us-gaap, e.g.
        defref_air_OperatingIncomeLossIncludingIncomeLossFromEquityMethodInvestments
      though this should be defref_us-gaap_OperatingIncomeLoss
   e. millions? Assume yes. th in first row with class="t1", div.strong:
        Consolidated Statements Of Operations - USD ($)<br> shares in Millions,
        $ in Millions
'''


def get_units_date_items(text):
    units = re.findall(r"(?:(?:I|i)n(?:[\s\n])?([A-Za-z]+))", text, re.MULTILINE)[0]
    try:
        dates = [datetime.strptime(re.sub(r"\s{2,}", " ", date), "%B %d, %Y") for date in
                 re.findall(r"\w+\s\d{1,2},\s+\d{4}", text)][:1]
        if not dates:
            raise
    except:
        dates = [datetime.strptime(date, "%B %d,%Y") for date in re.findall(r"\w+\s\d{1,2},\d{4}", text)][:1]
    items = [i.strip() for i in text.split("\n") if (i.strip() != "" and i.strip() != "$")]
    return dates, items, units, text


def _get_balance_sheet(text, months=12):
    dates, items, units, changed_text = get_units_date_items(text)
    extra = [item for item in items if "See Note" in item]
    if len(extra) > 0:
        maybe_relevant_items = items[:items.index(extra[0])]
    else:
        maybe_relevant_items = items
    maybe_relevant_items = [item for item in maybe_relevant_items if
                            not item.isupper() and item[0].isupper()]
    maybe_relevant_items = maybe_relevant_items[maybe_relevant_items.index('Current assets:'):]
    relevant_items = [item for item in maybe_relevant_items if not item.endswith(":")]
    relevant_items.remove("Commitments and contingencies")
    units_to_multiplier = {"millions": 1000000, "none": 1}
    data = {}
    for item in relevant_items:
        item_index = items.index(item)
        item_value = items[item_index + 1:item_index + 1 + len(dates)][0]
        if item_value == '—':
            item_value = "0"
        sign = -1 if item_value.startswith("(") and item_value.endswith(")") else 1
        adjusted_units = units
        if item.startswith("Accounts receivable"):
            item = "Accounts receivable"
        if item.startswith("Common stock"):
            item = "Common stock"
            adjusted_units = "none"  # TODO ensure that's actually the units
        data[item] = int(item_value.replace(",", "").strip("()")) * units_to_multiplier[adjusted_units] * sign
    if "Goodwill" not in data:
        data["Goodwill"] = 0
    if "Acquired intangible assets, net" not in data:
        data["Acquired intangible assets, net"] = 0
    return FinancialInfo(dates[0], months, {key: FinancialElement(key, val) for key, val in data.items()})


def _get_cash_flow(text, months=12):
    dates, items, units, changed_text = get_units_date_items(text)
    extra = [item for item in items if "See Note" in item]
    if len(extra) > 0:
        maybe_relevant_items = items[:items.index(extra[0])]
    else:
        maybe_relevant_items = items
    maybe_relevant_items = [item for item in maybe_relevant_items if
                            not item.isupper() and item[0].isupper()]
    if 'Cash and cash equivalents, beginning of the period' in maybe_relevant_items:
        maybe_relevant_items = maybe_relevant_items[
                               maybe_relevant_items.index('Cash and cash equivalents, beginning of the period'):]
    elif 'Cash and cash equivalents, beginning of the year' in maybe_relevant_items:
        maybe_relevant_items = maybe_relevant_items[
                               maybe_relevant_items.index('Cash and cash equivalents, beginning of the year'):]
    possible_parents = list((str.lower, ["Operating Activities:", "Investing activities:", "Financing activities:",
                                         "Supplemental cash flow disclosure:"]))
    relevant_items = [item for item in maybe_relevant_items if
                      (not item.endswith(":") or item.lower() in possible_parents)]
    units_to_multiplier = {"millions": 1000000}
    data = {}
    current_parent = None
    for item in relevant_items:
        if item.lower() in possible_parents:
            current_parent = item
            continue
        item_index = items.index(item)
        item_value = items[item_index + 1:item_index + 1 + len(dates)][0]
        if item_value == '—':
            item_value = "0"
        sign = -1 if item_value.startswith("(") and item_value.endswith(")") else 1
        if not current_parent:
            item_name = f"{current_parent} - {item}"
        else:
            item_name = item
        data[item_name] = int(item_value.replace(",", "").strip("()")) * units_to_multiplier[units] * sign
    return FinancialInfo(dates[0], months, {key: FinancialElement(key, val) for key, val in data.items()})


def _get_income_statement(text, months=12):
    dates, items, units, changed_text = get_units_date_items(text)
    extra = [item for item in items if "See Note" in item]
    if len(extra) > 0:
        maybe_relevant_items = items[:items.index(extra[0])]
    else:
        maybe_relevant_items = items
    maybe_relevant_items = [item for item in maybe_relevant_items if
                            not item.isupper() and item[0].isupper() and item != "Basic" and item != "Diluted"]
    relevant_items = maybe_relevant_items[maybe_relevant_items.index('Net sales'):]
    extra2 = [item for item in relevant_items if "Three Months Ended" in item]
    if len(extra2) > 0:
        relevant_items = relevant_items[:relevant_items.index(extra2[0])]
    relevant_items.remove("Operating expenses:")
    if "Other income and expense:" in relevant_items:
        relevant_items.remove("Other income and expense:")
    if 'Special charges:' in relevant_items:
        relevant_items.remove('Special charges:')
    units_to_multiplier = {"millions": 1000000, "thousands": 1000, "none": 1}
    data = {}
    for item in relevant_items:
        item_index = items.index(item)
        if items[item_index + 1] == "Basic":
            clean_item = item.strip(":")
            base_index = items[item_index:].index("Basic")
            diluted_index = items[item_index:].index("Diluted")
            base_value = items[item_index + base_index + 1:item_index + base_index + 1 + len(dates)][0]
            diluted_value = items[item_index + diluted_index + 1:item_index + diluted_index + 1 + len(dates)][0]
            adjusted_units = "thousands" if "per share" in clean_item else units
            data[clean_item] = int(
                float(base_value.replace(",", "").strip("()")) * units_to_multiplier[adjusted_units])
            data[clean_item + " Diluted"] = int(
                float(diluted_value.replace(",", "").strip("()")) * units_to_multiplier[adjusted_units])
        else:
            item_value = items[item_index + 1:item_index + 1 + len(dates)][0]
            if item_value == '—':
                item_value = "0"
            sign = -1 if item_value.startswith("(") and item_value.endswith(")") else 1
            item = re.sub(r" ?\(\d\)", "", item)
            if item not in data:
                adjusted_units = "none" if "dividends" in item else units
                value_type = float if "." in item_value else int
                data[item] = value_type(item_value.replace(",", "").strip("()")) * units_to_multiplier[
                    adjusted_units] * sign
    if "Preferred dividends" not in data:
        data["Preferred dividends"] = 0
    return FinancialInfo(dates[0], months, {key: FinancialElement(key, val) for key, val in data.items()})


def _process_old_financial_info(financial_html_text, months=12):
    try:
        source_soup = BeautifulSoup(financial_html_text, 'html.parser')
    except:
        pass
    nonBreakSpace = u'\xa0'
    report_text = source_soup.text.replace(nonBreakSpace, " ")
    report_text = re.sub(f"\n+\)", ")", report_text)
    report_text = report_text.replace('\' \'', " ")
    report_text = re.sub(r" +", " ", report_text)
    report_text = report_text.replace("’", "")
    report_text = re.sub(r",\n", ",", report_text)
    for f in re.findall(r"\w\n\s\w", report_text):
        report_text = report_text.replace(f, f.replace("\n", ""))
    for f in re.findall(r"\w+.*\n.*\w+", report_text):
        report_text = report_text.replace(f, f.replace("\n", ""))
    soo_start = report_text.find(re.findall("STATEMENTS ?OF ?OPERATIONS", report_text)[0])
    soo_end = report_text[soo_start:].find("See")
    soo_text = report_text[soo_start:soo_start + soo_end]
    bs_start = report_text.find(re.findall("BALANCE ?SHEETS", report_text)[0])
    bs_end = report_text[bs_start:].find("See")
    bs_text = report_text[bs_start:bs_start + bs_end]
    socf_start = report_text.find(re.findall("STATEMENTS ?OF ?CASH ?FLOW", report_text)[0])
    socf_end = report_text[socf_start:].find("See")
    if socf_end < 0:
        match = next(re.finditer(r"\n+\d+[\n\s]+[A-Z]{2,}", report_text[socf_start:]))
        socf_end = match.start()
    socf_text = report_text[socf_start:socf_start + socf_end]
    if not soo_text or not bs_text or not socf_text:
        raise
    balance_sheet_info = _get_balance_sheet(bs_text, months)
    income_statement_info = _get_income_statement(soo_text, months)
    numbers = re.findall(r"(\w+) Months Ended", socf_text, re.IGNORECASE)
    if len(numbers) > 0:
        months = w2n.word_to_num(numbers[0])
    cash_flow_info = _get_cash_flow(socf_text, months)
    return [balance_sheet_info, income_statement_info, cash_flow_info]


def get_old_financial_report(company, date_filed, financial_html_text, months=12):
    financial_info = _process_old_financial_info(financial_html_text, months)
    financial_report = FinancialReport(company, date_filed, financial_info)
    return financial_report


def get_financial_report(company, date_filed, financial_html_text):
    '''
    Returns a FinancialReport from html-structured financial data
    
    :param company: identifier of the company that the financial_html_text
        belongs to (can be the company's stock symbol, for example)
    :param date_filed: datetime representing ACCEPTANCE-DATETIME of Filing
    :param financial_html_text: html-structured financial data from an annual
        or quarterly Edgar filing
    '''
    financial_info = _process_financial_info(financial_html_text)
    financial_report = FinancialReport(company, date_filed, financial_info)
    return financial_report


def _process_financial_info(financial_html_text):
    '''
    Return a list of FinancialInfo objects from html-structured financial data
    
    :param financial_html_text: html-structured financial data from an annual
        or quarterly Edgar filing
    '''
    source_soup = BeautifulSoup(financial_html_text, 'html.parser')
    report = source_soup.find('table', {'class': 'report'})
    rows = report.find_all('tr')

    financial_info = []

    dates, period_units, unit_text = _get_statement_meta_data(rows)

    for i, date in enumerate(dates):
        dt = datetime.strptime(date, '%b. %d, %Y')
        financial_info.append(FinancialInfo(dt, period_units[i], {}))

    adjusted_label_map = {
        ('us-gaap_SalesRevenueNet', 'Net sales'): "Net sales",
        ('us-gaap_CostOfGoodsAndServicesSold', 'Cost of sales'): "Cost of sales",
        ('us-gaap_GrossProfit', 'Gross margin'): "Gross margin"
    }

    for row_num, row in enumerate(rows):
        data = row.find_all('td')

        xbrl_element = None
        label = None
        numeric_data_available = False

        for index, info in enumerate(data):
            info_text = info.get_text().strip()

            class_list = None
            try:
                # handle cases where the row is just a separator or something
                class_list = info.attrs['class']
            except KeyError as e:
                # print('KeyError {} from below table data, moving along'.format(e))
                # print(info)
                continue

            processed_financial_value = None

            if 'pl' in class_list:
                # pl class indicates the td is the financial label
                xbrl_element = _process_xbrl_element(info)
                # print(xbrl_element)
                label = info_text

            elif 'nump' in class_list or 'num' in class_list:
                # nump class indicates td, and so more generally, the row, has numeric data
                numeric_data_available = True
                processed_financial_value = _process_financial_value(info_text, xbrl_element, unit_text)

            elif 'text' in class_list:
                if numeric_data_available:
                    # this corner case occurs when a given element appears sparsely (e.g. not collected in every period)
                    processed_financial_value = _process_financial_value(info_text, xbrl_element, unit_text)
                # else:
                # 	# super label (abstract - no financial data)
                # 	print(xbrl_element)

            if processed_financial_value is not None:
                # print(index)
                if index - 1 not in range(len(financial_info)):
                    print('index-1 {} is too big to capture {}'.format(index - 1, processed_financial_value))
                financial_info_map = financial_info[index - 1].map

                if xbrl_element not in financial_info_map:
                    # handles adjustment details
                    # e.g. https://www.sec.gov/Archives/edgar/data/867773/0000867773-18-000082.txt
                    financial_info_map[xbrl_element] = FinancialElement(label, processed_financial_value)

    # clean reports
    # colspans sometimes cause duplicate reports with empty maps
    for fi in financial_info:
        if not fi.map:
            financial_info.remove(fi)

    return financial_info


def _get_statement_meta_data(rows):
    '''
    Returns the dates, period_units, unit_text given the html table rows of a
    financial statement filing

    :return: tuple of:
        dates - list of the different dates of the filing,
        period_units - list of the period (in months) that each date covers,
        unit_text - text that tells us the unit of shares and dollars being
            used in the filing
    '''
    dates = []
    period_units = []
    unit_text = None
    is_snapshot = False

    title_repeat = 0

    # all the meta data we need is in the first two tables rows
    for row_num, row in enumerate(rows[:2]):
        # meta data comes from the table headers
        data = row.find_all('th')

        for index, info in enumerate(data):
            info_text = info.get_text().replace('\n', '')

            class_list = info.attrs['class']

            repeat = 1 if 'colspan' not in info.attrs else int(info.attrs['colspan'])

            if row_num == 0:

                if 'tl' in class_list:
                    # first col is for xbrl_element, so we're concerned if it has a colspan greater than 1
                    # so that we can determine our table structure
                    title_repeat = 0 if 'colspan' not in info.attrs or int(info.attrs['colspan']) == 1 else int(
                        info.attrs['colspan']) - 1
                    # first th with tl class has title and unit specification
                    info_list = info.find('div').get_text('|', strip=True).split('|')
                    # e.g. shares in Thousands, $ in Millions
                    unit_text = info_list[1]
                    # e.g. CONSOLIDATED STATEMENTS OF INCOME - USD ($)
                    title = info_text.replace(unit_text, '').strip()

                    # Not yet using Statements.balance_sheets from filing.py because not sure
                    # if we can assume that the FilingSummary names will be consistent with the 
                    # title
                    if 'balance' in title.lower() or 'statement of financial position' in title.lower():
                        is_snapshot = True

                elif 'th' in class_list:
                    # Period unit of measurement (e.g. 12 Months Ended)
                    # Balance sheets are a snapshot, so no period
                    if index == 1:
                        # repeat just the first one to cover the excess title colspan
                        # use index 1 because 0 is title
                        repeat += title_repeat

                    for i in range(repeat):
                        if is_snapshot:
                            period_units.append(None)
                            dates.append(info_text)
                        else:
                            period_units.append(_process_period(info_text))


            elif row_num == 1 and 'th' in class_list:
                # second row indicates dates of data to come
                if index == 0:
                    # repeat just the first one to cover the excess title colspan
                    repeat += title_repeat

                for i in range(repeat):
                    dates.append(info_text)

    if len(dates) != len(period_units):
        raise MetaDataParsingException(
            'Potential parsing bug: len dates {} != len period_units {}'.format(dates, period_units))

    return dates, period_units, unit_text


def _process_period(info_text):
    '''
    Returns the number of months given a financial reporting period
    
    :param info_text: a reporting period, e.g. "12 Months Ended"
    '''
    return int(re.sub('[^0-9]', '', info_text))


def _process_xbrl_element(info):
    '''
    Returns the name of the XBRL element in info (html BeautifulSoup).
    Leaving "us-gaap_" prefix in so it's contains both the namespace
    and elementName of the XBRL (in case it's not always us-gaap)

    :param info: must be an html element with an anchor child that has an
        onclick attribute of the form: 
        onclick="top.Show.showAR( this, 'defref_<xbrl_name>', window );"
    :return: <xbrl_name>
    '''
    # us-gaap namespace element is in the onclick of the anchor tag
    anchor = info.find('a')
    onclick_attr = anchor.attrs['onclick']
    # strip javascript
    xbrl_element = onclick_attr.replace(
        'top.Show.showAR( this, \'defref_', ''
    ).replace('\', window );', '')

    return xbrl_element


def _process_financial_value(text, xbrl_element, unit_text):
    '''
    Returns float representation of text after stripping special characters

    :param text: the monetary value, which if in brackets, is negative
    :param xbrl_element: text of html element that contains xbrl info
        for the value of the text (i.e. the context)
    :param unit_text: text of the form "x in y" where
        x is either "shares" or "$"
        y is either "thousands", "millions", or "billions"
    '''
    is_negative = True if '(' in text else False
    # strip special characters
    amount_text = re.sub('[^0-9\\.]', '', text)
    value = None

    try:
        amount = float(amount_text)
        value = -amount if is_negative else amount

        # handle units
        if ('PerShare' in xbrl_element):
            value = value  # no change
        elif (('Shares' in xbrl_element and 'shares in billions' in unit_text.lower())
              or ('Shares' not in xbrl_element and '$ in billions' in unit_text.lower())):
            value = value * 1000000000
        elif (('Shares' in xbrl_element and 'shares in millions' in unit_text.lower())
              or ('Shares' not in xbrl_element and '$ in millions' in unit_text.lower())):
            value = value * 1000000
        elif (('Shares' in xbrl_element and 'shares in thousands' in unit_text.lower())
              or ('Shares' not in xbrl_element and '$ in thousands' in unit_text.lower())):
            value = value * 1000

    except ValueError:
        print('Warning: {} (from {}) is not numeric even after removing special characters () - ignoring'.format(text,
                                                                                                                 xbrl_element,
                                                                                                                 amount_text))

    return value
