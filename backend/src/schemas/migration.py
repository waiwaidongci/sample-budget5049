from pydantic import BaseModel,Field
class MethodSwitchInput(BaseModel):
    code:str=Field(min_length=2,max_length=40)
class MigrationCreateInput(BaseModel):
    target_method:str=Field(default='v2-fisher2',min_length=2,max_length=40)
