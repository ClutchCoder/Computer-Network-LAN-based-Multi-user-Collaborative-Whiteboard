"""
Color Palette & Brush Controls Toolbar Widget.
"""

import tkinter as tk
from tkinter import colorchooser, ttk
from typing import Callable, Optional

import config


class ColorPaletteWidget(ttk.Frame):
    """
    Toolbar strip containing color swatches, custom color picker, and brush width slider.
    """

    def __init__(
        self,
        parent: tk.Widget,
        initial_color: str = config.DEFAULT_STROKE_COLOR,
        initial_width: int = config.DEFAULT_STROKE_WIDTH,
        on_color_changed: Optional[Callable[[str], None]] = None,
        on_width_changed: Optional[Callable[[int], None]] = None,
    ):
        super().__init__(parent)
        self.current_color = initial_color
        self.current_width = initial_width
        self.on_color_changed = on_color_changed
        self.on_width_changed = on_width_changed

        self._swatch_buttons = {}
        self._build_ui()

    def _build_ui(self) -> None:
        # --- Color Selection Section ---
        color_frame = ttk.LabelFrame(self, text="Colors", padding=(6, 4))
        color_frame.pack(side=tk.LEFT, padx=6, pady=2)

        # Swatch grid
        swatch_container = tk.Frame(color_frame)
        swatch_container.pack(side=tk.LEFT, padx=(0, 6))

        row1 = tk.Frame(swatch_container)
        row1.pack(side=tk.TOP, pady=1)
        row2 = tk.Frame(swatch_container)
        row2.pack(side=tk.TOP, pady=1)

        half = len(config.PRESET_COLORS) // 2
        for i, color in enumerate(config.PRESET_COLORS):
            target_row = row1 if i < half else row2
            btn = tk.Button(
                target_row,
                bg=color,
                activebackground=color,
                width=2,
                height=1,
                relief=tk.RAISED,
                bd=2,
                cursor="hand2",
                command=lambda c=color: self.set_color(c),
            )
            btn.pack(side=tk.LEFT, padx=1)
            self._swatch_buttons[color] = btn

        # Custom Color Picker Button
        self.custom_btn = ttk.Button(color_frame, text="🎨 More...", width=8, command=self._pick_custom_color)
        self.custom_btn.pack(side=tk.LEFT, padx=4)

        # Current Color Indicator Box
        self.indicator_frame = tk.Frame(color_frame, bd=2, relief=tk.SUNKEN)
        self.indicator_frame.pack(side=tk.LEFT, padx=4)
        self.indicator_label = tk.Label(
            self.indicator_frame,
            bg=self.current_color,
            width=3,
            height=1,
            relief=tk.FLAT,
        )
        self.indicator_label.pack(fill=tk.BOTH, expand=True)

        # --- Brush / Stroke Width Section ---
        width_frame = ttk.LabelFrame(self, text="Stroke Width", padding=(6, 4))
        width_frame.pack(side=tk.LEFT, padx=6, pady=2)

        self.width_var = tk.IntVar(value=self.current_width)
        self.width_scale = ttk.Scale(
            width_frame,
            from_=1,
            to=40,
            variable=self.width_var,
            orient=tk.HORIZONTAL,
            length=110,
            command=self._on_scale_moved,
        )
        self.width_scale.pack(side=tk.LEFT, padx=4)

        self.width_display = ttk.Label(width_frame, text=f"{self.current_width}px", width=5)
        self.width_display.pack(side=tk.LEFT, padx=2)

        # Live width preview canvas dot
        self.preview_canvas = tk.Canvas(width_frame, width=32, height=32, bg="#FFFFFF", highlightthickness=1)
        self.preview_canvas.pack(side=tk.LEFT, padx=4)
        self._update_preview_dot()

    def set_color(self, hex_color: str) -> None:
        self.current_color = hex_color
        self.indicator_label.config(bg=hex_color)
        self._update_preview_dot()
        if self.on_color_changed:
            self.on_color_changed(hex_color)

    def _pick_custom_color(self) -> None:
        color_tuple = colorchooser.askcolor(
            color=self.current_color,
            title="Select Drawing Color",
            parent=self.winfo_toplevel(),
        )
        if color_tuple and color_tuple[1]:
            self.set_color(color_tuple[1])

    def _on_scale_moved(self, val: str) -> None:
        new_width = max(1, int(float(val)))
        self.current_width = new_width
        self.width_display.config(text=f"{new_width}px")
        self._update_preview_dot()
        if self.on_width_changed:
            self.on_width_changed(new_width)

    def _update_preview_dot(self) -> None:
        self.preview_canvas.delete("all")
        cx, cy = 16, 16
        r = max(1.0, self.current_width / 2.0)
        # Cap visual dot size so it fits preview box
        draw_r = min(14.0, r)
        self.preview_canvas.create_oval(
            cx - draw_r, cy - draw_r, cx + draw_r, cy + draw_r,
            fill=self.current_color,
            outline="#888888" if self.current_color.upper() == "#FFFFFF" else self.current_color,
        )
