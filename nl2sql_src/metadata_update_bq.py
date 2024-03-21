import json
from google.cloud import bigquery
import vertexai
from vertexai.language_models import TextGenerationModel

def generate_metadata(project_id, location, dataset_name, model_name="text-bison-32k", model_parameters=None):

    vertexai.init(project=project_id, location=location)
    parameters = {
    "max_output_tokens": 8192,
    "temperature": 0.9,
    "top_p": 1
    }

    model = TextGenerationModel.from_pretrained(model_name)
    client = bigquery.Client(project=project_id)

    data = {
        "Dataset Name": dataset_name,
        "Tables": {}
    }

    for table_ref in client.list_tables(dataset_name):
        try:
            table = client.get_table(table_ref)
        except Exception as e:
            print(f"Error getting table {table_ref}: {e}")
            continue

        table_name = table.reference.table_id

        table_description = table.description if table.description else ""
        if not table_description:
            prompt = f"Describe the data in table '{table_name}'."
            response = model.predict(prompt=prompt)
            table_description = response.text.strip()

        columns = {}
        schema = table.schema

        prompt = f"Describe the following columns in table '{table_name}':\n"
        for field in schema:
            prompt += f"- Column Name: {field.name}, Type: {field.field_type}\n"
        print(prompt)
        response = model.predict(prompt=prompt ,**parameters)
        print(response)
        column_descriptions = response.text.strip().split("\n\n")

        for i, field in enumerate(schema):
            column_description = column_descriptions[i] if i < len(column_descriptions) else ""
            column_dict = {
                'Name': field.name,
                'Type': field.field_type,
                'Description': column_description,
                'Examples': f"Sample value for {field.name}"  # Can be updated with actual samples later
            }
            columns[field.name] = column_dict

        data["Tables"][table_name] = {
            "Table Name": table_name,
            "Description": table_description,
            "Columns": columns
        }

    return data

if __name__ == "__main__":
    metadata = generate_metadata(
        project_id="cdii-poc",
        location="us-central1",
        dataset_name="cdii-poc.qnadb"
    )

    with open('updated-metadata.json', 'w') as f:
        json.dump(metadata, f, indent=4)

    print(f'JSON file created successfully: updated-metadata.json')