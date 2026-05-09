# core/class_mapper.py
import yaml

class ClassMapper:
    def __init__(self, config_path="config/classes.yaml"):
        with open(config_path, 'r') as f:
            data = yaml.safe_load(f)
        self.id_to_name = {int(k): v for k, v in data.get('classes', {}).items()}
        self.name_to_color = data.get('colors', {})
        self.default_color = self.name_to_color.get('default', [128,128,128])

    def get_name(self, class_id):
        return self.id_to_name.get(class_id, f"Class_{class_id}")

    def get_color(self, class_name):
        return self.name_to_color.get(class_name, self.default_color)

    def get_color_by_id(self, class_id):
        name = self.get_name(class_id)
        return self.get_color(name)