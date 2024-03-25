import re
import json
import traceback
import sqlalchemy
import pandas as pd
import langchain
import sqlglot
from langchain_google_vertexai import VertexAI
from google.cloud import bigquery
from .nl2sql_query_embeddings import PgSqlEmb

client = bigquery.Client()


class Nl2sqlBq_rag:
    "Bigquery nl2sql class"

    def __init__(self, project_id, dataset_id,metadata_json_path=None, model_name = "gemini-pro"):
        "Init function"
        self.dataset_id = f"{project_id}.{dataset_id}"
        self.metadata_json = None
        self.model_name = model_name
        self.llm = VertexAI(temperature=0, model_name=self.model_name, max_output_tokens=1024)
        self.engine = sqlalchemy.engine.create_engine(
            f"bigquery://{self.dataset_id.replace('.','/')}")
        if metadata_json_path:
            f = open(metadata_json_path, encoding="utf-8")
            self.metadata_json = json.loads(f.read())
            
        # self.pge = PgSqlEmb("cdii-poc", "us-central1", "cdii-demo-temp", "demodbcdii", "postgres", "cdii-demo")

    def init_pgdb(self, proj_id, loc, pg_inst, pg_db, pg_uname, pg_pwd, index_file='saved_index_pgdata'):
        # self.pge = PgSqlEmb("cdii-poc", "us-central1", "cdii-demo-temp", "demodbcdii", "postgres", "cdii-demo")
        self.pge = PgSqlEmb(proj_id, loc, pg_inst, pg_db, pg_uname, pg_pwd)
        
        
    def get_all_table_names(self):
        "Provides list of table names in dataset"
        tables = client.list_tables(self.dataset_id)
        all_table_names = [table.table_id for table in tables]
        return all_table_names

    def get_column_value_examples(self, tname, column_name, enum_option_limit):
        "Provide example values for string columns"
        examples_str =""
        if pd.read_sql(
        sql=f"SELECT COUNT(DISTINCT {column_name}) <= {enum_option_limit} FROM `{tname}`",
        con=self.engine
        ).values[0][0]:
            examples_str = "It contains values : \"" + ("\", \"".join(
                            filter(
                                lambda x: x is not None,
                                pd.read_sql(
                                    sql=f"SELECT DISTINCT {column_name} AS vals FROM `{tname}`",
                                    con=self.engine
                                    )["vals"].to_list()
                            )
                            )
                        ) + "\"."
        return examples_str

    def create_metadata_json(self,metadata_json_dest_path, data_dict_path=None,
                             col_values_distribution=False,enum_option_limit=10):
        "Creates metadata json file"
        try:
            data_dict = dict()
            if data_dict_path:
                f = open(data_dict_path,encoding="utf-8")
                data_dict = json.loads(f.read())
            table_ls = self.get_all_table_names()
            metadata_json = dict()
            for table_name in table_ls:
                table=client.get_table(f"{self.dataset_id}.{table_name}")
                print(table_name)
                # print(table.description)
                table_description = ""
                if table_name in data_dict and data_dict[table_name].strip():
                    table_description = data_dict[table_name]
                elif table.description:
                    table_description = table.description
                columns_info = dict()

                for schema in table.schema:
                    #print(dir(schema))
                    print(schema.name)
                    # print(schema.field_type)
                    # print(schema.description)
                    #print(df[schema.name])
                    schema_description = ""
                    if f"{table_name}.{schema.name}" in data_dict and \
                        data_dict[f"{table_name}.{schema.name}"].strip():
                        schema_description = data_dict[f"{table_name}.{schema.name}"]
                    elif schema.description:
                        schema_description = schema.description
                    columns_info[schema.name] = {"Name":schema.name,"Type":schema.field_type,
                                                 "Description":schema_description,"Examples":""}
                    if col_values_distribution and schema.field_type == "STRING":
                        all_examples = self.get_column_value_examples(
                            table_name, schema.name, enum_option_limit)
                        columns_info[schema.name]["Examples"] = all_examples
                metadata_json[table_name]={"Name":table_name,"Description":table_description,
                                           "Columns":columns_info}
            with open(metadata_json_dest_path, 'w',encoding="utf-8") as f:
                json.dump(metadata_json, f)
            self.metadata_json = metadata_json
        except Exception as exc:
            raise Exception(traceback.print_exc()) from exc

    def table_filter(self,question):
        """
        This function selects the relevant table(s) to the provided question 
        based on their description-keywords. 
        It assists in selecting a table from a list of tables based on their description-keywords.
        It presents a prompt containing a list of table names along 
        with their corresponding description-keywords. 
        The function uses a text-based model (text_bison) to analyze the prompt 
        and extract the selected table name(s).

        Parameters:
        - question (str): The question for which the relevant table need to be identified.

        Returns:
        list: A list of table names most likely relevant to the provided question.
        """

        only_tables_info = ""

        for table in self.metadata_json:
            only_tables_info = only_tables_info + f"{table} | \
                {self.metadata_json[table]['Description'].strip()}. It contains columns: \
                {', '.join([c for c in list(self.metadata_json[table]['Columns'].keys()) if len(c) >4])}\n"

        prompt = f'''
You are a database expert at selecting a table from a list of tables based on their description.
For the provided question choose what is the table_name most likely to be relevant.
Only mention the table name from the following list and their description.
Output should be only 1 table that is the most likely table to contain the relevant data
Do not mention anything more than the table name.

Table name | description
{only_tables_info}

Question: {question}
'''
        # print(prompt)
        result = self.llm.invoke(prompt)
        #print(result)

        segments = result.split(',')
        tables_list = []

        for segment in segments:
            segment = segment.strip()
            if ':' in segment:
                value = segment.split(':')[-1].strip()
                tables_list.append(value.strip())
            else:
                tables_list.append(segment)
        print("Table Filter - ", tables_list)
        return tables_list

    def case_handler_transform(self,sql_query: str) -> str:
        """
        This function implements case-handling mechanism transformation for a SQL query.

        Parameters:
        - sql_query (str): The original SQL query.

        Returns:
        str: The transformed SQL query with case-handling mechanism applied, 
            or the original query if no transformation is needed.
        """
        node = sqlglot.parse_one(sql_query)

        if (
          isinstance(node, sqlglot.expressions.EQ) and
          node.find_ancestor(sqlglot.expressions.Where) and
          len(operands := list(node.unnest_operands())) == 2 and
          isinstance(literal := operands.pop(), sqlglot.expressions.Literal) and
          isinstance(predicate := operands.pop(), sqlglot.expressions.Column)
        ):
            transformed_query = sqlglot.parse_one(f"LOWER({predicate}) = '{literal.this.lower()}'")
            return str(transformed_query)
        else:
            return sql_query

    def add_dataset_to_query(self,sql_query):
        """
        This function adds the specified dataset prefix to the tables
        in the FROM clause of a SQL query.

        Parameters:
        - dataset (str): The dataset name to be added as a prefix.
        - sql_query (str): The original SQL query.

        Returns:
        str: Modified SQL query with the specified dataset prefix 
        added to the tables in the FROM clause.
        """
        dataset = self.dataset_id
        if sql_query:
            # Define a regular expression pattern to match the FROM clause
            pattern = re.compile(r'\bFROM\b\s+`?([\w-]+)`?', re.IGNORECASE)

            # Find all matches of the pattern in the SQL query
            matches = pattern.findall(sql_query)
            

            # Iterate through matches and replace the table name
            for match in matches:
                next_text = sql_query.split(match)[1].split('\n')[0]
                next_text = next_text.split(' ')[0]
                # print("total string text = ", next_text, match)
                # Check if the previous word is not DAY, YEAR, or MONTH
                if re.search(r'\b(?:DAY|YEAR|MONTH)\b',
                             sql_query[:sql_query.find(match)], re.IGNORECASE) is None:
                    # Replace the next word after FROM with dataset.table

                    if match == dataset.split('.')[0]: # checking if in generated SQL, table includes the project-id and dataset or not
                        replacement = f'`{match}'
                    else:
                        replacement = f'{dataset}.`{match}`'

                    # Backticks causing issue
                    #replacement = f'`{dataset}.{match}`'
                    sql_query = re.sub(r'\bFROM\b\s+' + re.escape(match),
                                        f'FROM {replacement}', sql_query, flags=re.IGNORECASE)
                    
                    if match == dataset.split('.')[0]:
                        sql_query = sql_query.replace(f'{match}{next_text}', f'{match}{next_text}`')
            # print("Add_Dataset_to_query ", sql_query)  
            return sql_query
        else:
            return ""

    def text_to_sql(self,question, table_name = None, logger_file = "log.txt"):
        "Converts natural language question to sql query"
        print(question)
        try:
            if not table_name:
                if len(self.metadata_json.keys())>1:
                    table_list = self.table_filter(question)
                    table_name = table_list[0]
                else:
                    table_name = list(self.metadata_json.keys())[0]
            table_json = self.metadata_json[table_name]
            columns_json = table_json["Columns"]
            columns_info = ""
            for column_name in columns_json:
                column = columns_json[column_name]            
                column_info = f"""{column["Name"]} \
                    ({column["Type"]}) : {column["Description"]}. {column["Examples"]}\n"""
                columns_info = columns_info + column_info
                
            key = 'Name'

            table_str = f'''
Table Name : {table_json[key]}

Description: {table_json["Description"]}

This table has the following columns : 
{columns_info}
\n
'''
            sql_prompt = f'''
Only use the following tables meta-data:

```
{table_str}
```

**Instructions:**
* Generate syntactically correct BigQuery SQL for the following question.
* Use only relevant columns from the schema. DO NOT use columns not mentioned in this meta-data.
* Use only relevant table name as provided and do not qualify tablename with backticks(`).
* Validate column names against the schema to avoid errors.
* Use a maximum of 10 columns.
* Prioritize keywords that indicate calculations.
    

For this question what would be the most accurate SQL query?
Question: {question}'''
            # print(sql_prompt)
            response = self.llm.invoke(sql_prompt)
            sql_query = response.replace('sql', '').replace('```', '').replace('`','')
            # print("Text_to_Sql query :", sql_query)
            # sql_query = self.case_handler_transform(sql_query)
            sql_query = self.add_dataset_to_query(sql_query)
            with open(logger_file, 'a',encoding="utf-8") as f:
                f.write(f">>>>\nModel:{self.model_name} \n\nQuestion: {question}\
                         \n\nPrompt:{sql_prompt} \n\nSql_query:{sql_query}<<<<\n\n\n")
            return sql_query
        except Exception as exc:
            raise Exception(traceback.print_exc()) from exc


    def text_to_sql_fewshot(self,question, table_name = None, logger_file = "log.txt"):
        "Converts natural language question to sql query"
        print(question)
        # self.pge.recreate_vectordb_index()
        
        
        try:
            if not table_name:
                if len(self.metadata_json.keys())>1:
                    table_list = self.table_filter(question)
                    table_name = table_list[0]
                else:
                    table_name = list(self.metadata_json.keys())[0]
                    
            print("Table name ", table_name)
            
            table_json = self.metadata_json[table_name]
            columns_json = table_json["Columns"]
            columns_info = ""
            for column_name in columns_json:
                column = columns_json[column_name]            
                column_info = f"""{column["Name"]} \
                    ({column["Type"]}) : {column["Description"]}. {column["Examples"]}\n"""
                columns_info = columns_info + column_info
                
            few_shot_json = self.pge.search_matching_queries(question)
            few_shot_examples = ""
            for item in few_shot_json:
                example_string = f"Question: {item['question']}"
                few_shot_examples += example_string + "\n"
                example_string = f"SQL : {item['sql']} "
                few_shot_examples += example_string + "\n\n"

            # print(few_shot_examples)
            key = 'Name'

            
            table_str = f'''
Table Name : {table_json[key]}

Description: {table_json["Description"]}

This table has the following columns : 
{columns_info}
\n
'''
            sql_prompt = f'''
Only use the following tables meta-data:

```
{table_str}
```

**Instructions:**
* Generate syntactically correct BigQuery SQL for the following question.
* Use only relevant columns from the schema. DO NOT use columns not mentioned in this meta-data.
* Use only relevant table name as provided and do not qualify tablename with backticks(`).
* Validate column names against the schema to avoid errors.
* Use a maximum of 10 columns.
* Prioritize keywords that indicate calculations.
* Use the following examples as guidelines to generate the new BigQuery SQL accordingly

{few_shot_examples}
    

For this question what would be the most accurate BigQuery SQL query?
Question: {question}'''
            
            # print(sql_prompt)
            response = self.llm.invoke(sql_prompt)
            sql_query = response.replace('sql', '').replace('```', '').replace('`','')
            # print("Text_to_SQL_Fewshot query :", sql_query)
            # sql_query = self.case_handler_transform(sql_query)
            sql_query = self.add_dataset_to_query(sql_query)
            with open(logger_file, 'a',encoding="utf-8") as f:
                f.write(f">>>>\nModel:{self.model_name} \n\nQuestion: {question}\
                         \n\nPrompt:{sql_prompt} \n\nSql_query:{sql_query}<<<<\n\n\n")
            return sql_query
        except Exception as exc:
            raise Exception(traceback.print_exc()) from exc
            
            
            
    def execute_query(self,query):
        """
        This function executes an SQL query using the configured BigQuery client.

        Parameters:
        - query (str): The SQL query to be executed.

        Returns:
        pandas.DataFrame: The result of the executed query as a DataFrame.
        """
        try:
            # Run the SQL query
            query_job = client.query(query)

            # Wait for the job to complete
            query_job.result()

            # Fetch the result if needed
            results = query_job.to_dataframe()

            return results
        except Exception as exc:
            raise Exception(traceback.print_exc()) from exc

    def text_to_sql_execute(self,question, table_name = None, logger_file = "log.txt"):
        "Converts text to sql and also executes sql query"
        try:
            query = self.text_to_sql(question,table_name,logger_file = logger_file)
            print(query)
            results = self.execute_query(query)
            return results
        except Exception as exc:
            pass
            raise Exception(traceback.print_exc()) from exc

    def result2nl(self,result, question, insight=True):
        """
        The function converts an SQL query result into an insightful 
        and well-explained natural language summary, using text-bison model.

        Parameters:
        - result (str): The result of the SQL query.
        - question (str): The natural language question corresponding to the SQL query.

        Returns:
        str: A natural language summary of the SQL query result.
        """
        try:
            if insight:
                prompt = f'''
You are an expert Data Analyst. Given a report of SQL query and the question in
natural language, provide a very insightful, intuitive and a not too long well-explained summary of the
result which would help the user understand the result better and take informed decisions. 
If the result does not have any data, then just mention that briefly in the summary.
question: {question}
result: {str(result)}'''
            else:
                prompt = f'''
You are an expert Data Analyst. Given a report of SQL query and the question in
natural language, provide a very crisp, short, intuitive and easy-to-understand summary of the result.
If the result does not have any data, then just mention that briefly in the summary.
question: {question}
result: {str(result)}
'''

            return self.llm.invoke(prompt)
        except Exception as exc:
            raise Exception(traceback.print_exc()) from exc
        

    def main(self, query, execute=False, insight=False):
        """
        This function converts an SQL query into a natural language description,
        using text-bison model.
        Parameters:
        - query (str): The SQL query to be converted.
        - execute (bool): Whether to execute the SQL query.
        - insight (bool): Whether to provide an insightful summary of the result.

        Returns:
        str: A natural language description of the SQL query.
        """
        try:
            sql = self.text_to_sql(query)
            if execute:
                result = self.execute_query(sql)
                nl = self.result2nl(result, query, insight)
            else:
                result, nl = None, None
            return sql, result, nl
        except Exception as exc:
            raise Exception(traceback.print_exc()) from exc
        

    def auto_verify(self,nl_description, ground_truth, llm_amswer):
        """
        This function verifies the accuracy of SQL query based on a natural language description
        and a ground truth query, using text-bison model.

        Parameters:
        - nl_description (str): The natural language description of the SQL query.
        - ground_truth (str): The ground truth SQL query.
        - llm_amswer (str): The student's generated SQL query for validation.

        Returns:
        str: "Yes" if the student's answer matches the ground truth 
        and fits the NL description correctly,"No" otherwise.
        """

        prompt = f'''
You are an expert at validating SQL queries. Given the Natrual language description
and the SQL query corresponding to that description, please check if the students answer is correct.
There can be different ways to achieve the same result by forming the query differently.
If the students SQL query matches the ground truth and fits the NL description correctly, then return yes
else return no.
Natural language description: {nl_description}
Ground truth: {ground_truth}
students answer: {llm_amswer}
        '''
        return self.llm.invoke(prompt)

    def batch_run(self,test_file_name, output_file_name,execute_query=False, 
                  result2nl=False, insight=True,logger_file = "log.txt"):
        """
        This function procesess a batch of questions from a test file, 
        generate SQL queries, and evaluate their accuracy. 
        It reads questions from a CSV file, generates SQL queries using the `gen_sql` function, 
        evaluates the accuracy of the generated queries using the `auto_verify` function, 
        and optionally converts SQL queries to natural language 
        using the `sql2result` and `result2nl` functions. 
        The results are stored in a DataFrame and saved to a CSV file in the 'output' directory, 
        with a timestamped filename.

        Parameters:
        - test_file_name (str): 
        The name of the CSV file containing test questions and ground truth SQL queries.
        
        - sql2nl (bool, optional): 
        Flag to convert SQL queries to natural language. Defaults to False.

        Returns:
        pandas.DataFrame: A DataFrame containing question, ground truth SQL, 
        LLM-generated SQL, LLM rating, SQL execution result, and NL response.
        """
        try:
            questions = pd.read_csv(test_file_name)

            out = []
            columns = ['question', 'ground_truth', 'llm_response', 'llm_rating']
            if execute_query:
                columns.append('sql_result')
            if result2nl:
                columns.append('nl_response')
            for _, row in questions.iterrows():
                table_name = None
                if row["table"].strip():
                    table_name = row["table"]
                question = row["question"]
                print(question)
                sql_gen  = self.text_to_sql(question, table_name = table_name,
                                            logger_file = logger_file)
                print(sql_gen)
                rating = self.auto_verify(question, row["ground_truth_sql"], sql_gen)
                row_result = [question, row["ground_truth_sql"], sql_gen, rating]
                if execute_query:
                    result = self.execute_query(sql_gen)
                    print(result)
                    row_result.append(result)
                if execute_query and result2nl:
                    nl = self.result2nl(result, question, insight=insight)
                    row_result.append(nl)
                out.append(row_result)
                print("\n\n")

            df = pd.DataFrame(out, columns= columns)
            df.to_csv(output_file_name, index=False)
            return df
        except Exception as exc:
            raise Exception(traceback.print_exc()) from exc

