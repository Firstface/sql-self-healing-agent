from pydantic import BaseModel, ValidationError


def validate_structured_output(payload: object, response_model: type[BaseModel]) -> BaseModel:
    try:
        return response_model.model_validate(payload)
    except ValidationError:
        raise
