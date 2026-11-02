class MemeSenderPrompts:
    """Prompts; shared runtime resources belong to the facade."""

    def _build_meme_prompt(self, category_mapping_string: str | None = None) -> str:
        """Compose category instructions, examples, and optional quantity guidance.

        Args:
            category_mapping_string: Available categories for the current request.

        Returns:
            Category instructions followed by enabled, nonempty additions.
        """
        mapping_string = category_mapping_string or self.category_mapping_string
        prompt = self.prompt_head + mapping_string + self.prompt_tail
        for enabled, template in getattr(self, "prompt_examples", []):
            if enabled and template.strip():
                prompt += "\n\n" + template.strip()
        return prompt + self._build_meme_quantity_prompt()

    def _build_meme_quantity_prompt(self) -> str:
        """Build optional quantity guidance shared by every meme selection prompt.

        Returns:
            Editable quantity guidance, or an empty string when disabled or blank.
        """
        if not self._read_config_value(
            ("generation", "prompt", "quantity_guidance_enabled"), default=False
        ):
            return ""
        template = self._read_config_value(
            ("generation", "prompt", "quantity_guidance"), default=""
        ).strip()
        if not template:
            return ""
        return "\n\n" + template + "\n"
