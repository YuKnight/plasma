#!/usr/bin/env python3
#
# Reverse : Generate an indented asm code (pseudo-C) with colored syntax.
# Copyright (C) 2015    Joel
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.    See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.    If not, see <http://www.gnu.org/licenses/>.
#

import curses
from curses import A_UNDERLINE, color_pair
from time import time
from queue import Queue

from lib import init_entry_addr, disasm
from custom_colors import *
from lib.disassembler import NB_LINES_TO_DISASM

from lib.ui.window import *


class Visual(Window):
    def __init__(self, console, disassembler, output):
        Window.__init__(self, output, has_statusbar=True)

        self.mode = MODE_DUMP
        self.dis = disassembler
        self.console = console
        self.queue_wait_analyzer = Queue()

        # Last/first address printed (only in MODE_DUMP)
        self.last_addr = max(output.addr_line)
        self.first_addr = min(output.addr_line)

        self.stack = []
        self.saved_stack = [] # when we enter, go back, then re-enter

        new_mapping = {
            b"z": self.main_cmd_line_middle,
            b"g": self.main_k_top,
            b"G": self.main_k_bottom,
            b";": self.view_inline_comment_editor,
            b"%": self.main_cmd_next_bracket,
            b"\n": self.main_cmd_enter,
            b"\x1b": self.main_cmd_escape,
            b"\t": self.main_cmd_switch_mode,
            b"c": self.main_cmd_code,
            b"p": self.main_cmd_set_function,
            b"{": self.main_k_prev_paragraph,
            b"}": self.main_k_next_paragraph,
            b"x": self.main_cmd_xrefs,

            # I wanted ctrl-enter but it cannot be mapped on my terminal
            b"u": self.main_cmd_reenter, # u for undo
        }

        self.mapping.update(new_mapping)

        self.inline_mapping = {
            b"\x1b\x5b\x44": self.inline_k_left,
            b"\x1b\x5b\x43": self.inline_k_right,
            b"\x7f": self.inline_k_backspace,
            b"\x1b\x5b\x37\x7e": self.inline_k_home,
            b"\x1b\x5b\x38\x7e": self.inline_k_end,
            b"\x1b\x5b\x33\x7e": self.inline_k_delete,
            b"\x15": self.inline_k_ctrl_u,
            b"\x0b": self.inline_k_ctrl_k,
            b"\n": self.inline_k_enter,
            b"\x01": self.inline_k_home, # ctrl-a
            b"\x05": self.inline_k_end, # ctrl-e
        }

        saved_quiet = self.console.ctx.quiet
        self.console.ctx.quiet = True

        self.screen = curses.initscr()

        (h, w) = self.screen.getmaxyx()
        h -= 1 # status bar
        self.goto_address(self.first_addr, h, w)

        curses.noecho()
        curses.cbreak()
        curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)

        curses.start_color()
        curses.use_default_colors()

        if console.ctx.color:
            for i in range(0, curses.COLORS):
                curses.init_pair(i, i, -1)
            curses.init_pair(1, 253, 66) # for the highlight search
        else:
            for i in range(0, curses.COLORS):
                curses.init_pair(i, 7, -1) # white

        curses.wrapper(self.start_view)

        curses.nocbreak()
        curses.echo()
        curses.endwin()

        self.console.ctx.quiet = saved_quiet

        if self.stack:
            print("last address seen 0x%x" % self.console.ctx.entry_addr)


    def exec_disasm(self, addr, h):
        self.console.ctx.reset_vars()
        self.console.ctx.entry_addr = addr

        if self.mode == MODE_DUMP:
            self.console.ctx.dump = True
            o = self.dis.dump_asm(self.console.ctx, NB_LINES_TO_DISASM)
            self.console.ctx.dump = False

        elif self.mode == MODE_DECOMPILE:
            self.status_bar("decompiling...", h, True)
            self.console.ctx.dump = False
            o = disasm(self.console.ctx)

        if o is not None:
            self.output = o
            self.token_lines = o.token_lines
            self.last_addr = max(o.addr_line)
            self.first_addr = min(o.addr_line)
            return True
        return False


    def view_inline_comment_editor(self, h, w):
        line = self.win_y + self.cursor_y
        if line not in self.output.line_addr:
            return True

        self.console.ctx.db.modified = True

        addr = self.output.line_addr[line]

        # The same address can be repeated on multiple lines
        # With this we are sure to everytime on the same line
        # example for mips:
        #   # 0x4002bc: lui $gp, 0x19
        #   # 0x4002c0: addiu $gp, $gp, -0x63dc
        #   0x4002bc: li $gp, 0x189c24
        new_line = self.output.addr_line[addr]

        if new_line != line:
            self.goto_line(new_line, h)
            line = new_line
            (h, w) = self.screen.getmaxyx()
            self.view_main_redraw(h, w)

        # xbegin is the first index just after all operands
        # So we need to add 1 to be at the index of ;
        xbegin, _ = self.output.index_end_inst[line]
        self.comm = []
        screen = self.screen
        y = self.cursor_y
        col_intern = color_pair(COLOR_USER_COMMENT.val) | A_UNDERLINE

        if addr in self.dis.user_inline_comments:
            self.comm = list(self.dis.user_inline_comments[addr])

        self.cursor_x = xbegin + 3
        i = 0 # index in the string comment

        self.status_bar("-- INLINE COMMENT --", h)

        while 1:
            (h, w) = screen.getmaxyx()
            h -= 1 # status bar

            x = xbegin
            screen.move(y, xbegin)

            if x + 3 < w:
                screen.addstr(y, x, " ; ", col_intern)
                x += 3
                if x + len(self.comm) >= w:
                    screen.addstr("".join(self.comm[:w - x - 1]), col_intern)
                else:
                    screen.addstr("".join(self.comm), col_intern)
            screen.clrtoeol()

            # Underline the rest of the line
            n = w - xbegin - len(self.comm) - 4
            self.screen.addstr(" " * n, color_pair(0) | curses.A_UNDERLINE)

            if self.cursor_x >= w:
                self.cursor_x = w - 1
            screen.move(y, self.cursor_x)
            k = self.read_escape_keys()

            if k == b"\x1b": # escape = cancel
                break

            if k in self.inline_mapping:
                i = self.inline_mapping[k](xbegin, i, w)
                if k == b"\n":
                    break
            elif k[0] >= 32 and k[0] <= 126 and self.cursor_x < w - 1:
                # TODO: fix self.cursor_x >= w
                # TODO: utf-8
                c = chr(k[0])
                self.comm.insert(i, c)
                i += 1
                self.cursor_x += 1

        return True


    def dump_update_up(self, wy, h):
        if self.mode != MODE_DUMP or wy > 10:
            return wy

        if self.first_addr == self.dis.binary.get_first_addr():
            return wy

        ad = self.dis.find_addr_before(self.first_addr)

        self.console.ctx.entry_addr = ad
        self.console.ctx.dump = True
        o = self.dis.dump_asm(self.console.ctx, until=self.first_addr)
        self.console.ctx.dump = False

        if o is not None:
            nb_new_lines = len(o.lines)

            if self.win_y == 0 and self.cursor_y < 3:
                if nb_new_lines >= 3:
                    diff = 3 - self.cursor_y
                    self.cursor_y = 3
                    wy -= diff
                    self.win_y -= diff
                else:
                    self.cursor_y = nb_new_lines

            if nb_new_lines >= 3:
                wy += nb_new_lines
                self.win_y += nb_new_lines

            # Update line counter on each line

            self.output.lines = o.lines + self.output.lines
            self.output.token_lines = o.token_lines + self.output.token_lines
            self.first_addr = min(o.addr_line)

            for ad, l in self.output.addr_line.items():
                o.line_addr[nb_new_lines + l] = ad
                o.index_end_inst[nb_new_lines + l] = self.output.index_end_inst[l]
                o.addr_line[ad] = nb_new_lines + l

            self.output.line_addr.clear()
            self.output.addr_line.clear()
            self.output.index_end_inst.clear()
            self.output.line_addr = o.line_addr
            self.output.addr_line = o.addr_line
            self.output.index_end_inst = o.index_end_inst
            self.token_lines = self.output.token_lines

        return wy


    def dump_update_bottom(self, wy, h):
        if self.mode != MODE_DUMP or wy < len(self.token_lines) - h - 10:
            return

        if self.last_addr == self.dis.binary.get_last_addr():
            return

        self.console.ctx.reset_vars()

        if self.dis.mem.is_code(self.last_addr):
            inst = self.dis.lazy_disasm(self.last_addr)
            self.console.ctx.entry_addr = self.last_addr + inst.size
        else:
            self.console.ctx.entry_addr = self.last_addr + 1

        self.console.ctx.dump = True
        o = self.dis.dump_asm(self.console.ctx, NB_LINES_TO_DISASM)
        self.console.ctx.dump = False

        if o is not None:
            nb_new_lines = len(self.output.lines)

            self.output.lines += o.lines
            self.output.token_lines += o.token_lines
            self.last_addr = max(o.addr_line)

            for l in o.line_addr:
                self.output.line_addr[nb_new_lines + l] = o.line_addr[l]
                self.output.addr_line[o.line_addr[l]] = nb_new_lines + l
                self.output.index_end_inst[nb_new_lines + l] = o.index_end_inst[l]


    # New mapping


    def main_k_prev_paragraph(self, h, w):
        l = self.win_y + self.cursor_y - 1
        while l > 0 and len(self.output.lines[l]) != 0:
            l -= 1
        if l >= 0:
            self.goto_line(l, h)
            self.check_cursor_x()
        return True

    def main_k_next_paragraph(self, h, w):
        l = self.win_y + self.cursor_y + 1
        while l < len(self.output.lines)-1 and len(self.output.lines[l]) != 0:
            l += 1
        if l < len(self.output.lines):
            self.goto_line(l, h)
            self.check_cursor_x()
        return True


    def main_cmd_line_middle(self, h, w):
        mid = int(h/2)
        if self.cursor_y + self.win_y > mid:
            self.win_y += self.cursor_y - mid
            self.cursor_y = mid
        return True


    def main_k_top(self, h, w):
        self.stack.append(self.__compute_curr_position())
        self.saved_stack.clear()

        if self.mode == MODE_DUMP:
            top = self.dis.binary.get_first_addr()
            if self.first_addr != top:
                self.exec_disasm(top, h)

        Window.k_top(self, h, w)
        return True


    def main_k_bottom(self, h, w):
        self.stack.append(self.__compute_curr_position())
        self.saved_stack.clear()

        self.cursor_x = 0

        if self.mode == MODE_DUMP:
            bottom = self.dis.binary.get_last_addr()
            if self.last_addr != bottom:
                ad = self.dis.find_addr_before(bottom)
                self.exec_disasm(ad, h)
                self.win_y = 0
                self.cursor_y = 0

        Window.k_bottom(self, h, w)
        return True


    def main_cmd_next_bracket(self, h, w):
        # TODO: fix self.cursor_x >= w
        line = self.win_y + self.cursor_y
        line_str = self.output.lines[line]
        x = self.cursor_x
        if x >= len(line_str):
            x = len(line_str) - 1
        char = line_str[x]
        new_line = -1

        if char == "}":
            l = line - 1
            while l >= 0:
                if self.output.lines[l][x] != " ":
                    new_line = l
                    break
                l -= 1

            if l != -1:
                x = 0
                while x < len(self.output.lines[l]):
                    if self.output.lines[l][x] == "{":
                        break
                    x += 1

            self.cursor_x = x

        elif char == "{":
            x = 0
            while 1:
                if self.output.lines[line][x] != " ":
                    break
                x += 1

            l = line + 1
            while l < len(self.output.lines):
                if self.output.lines[l][x] != " ":
                    new_line = l
                    break
                l += 1

            self.cursor_x = x

        if new_line != -1:
            self.goto_line(new_line, h)

        return True


    def __compute_curr_position(self):
        line = self.win_y + self.cursor_y
        if self.mode == MODE_DECOMPILE:
            last = self.console.ctx.entry_addr
            offset_y = (self.win_y, self.cursor_y)
        else:
            # We save only addresses on the stack, so if the cursor is
            # not on a line with an address, we search the nearest line
            # and save an offset to the original line.
            offset_y = 0
            while line not in self.output.line_addr:
                line += 1
                offset_y += 1
            last = self.output.line_addr[line]
        return (last, self.cursor_x, self.mode, offset_y)


    def main_cmd_enter(self, h, w):
        word = self.get_word_under_cursor()
        if word is None:
            return False

        topush = self.__compute_curr_position()

        if word.startswith("0x"):
            try:
                ad = int(word, 16)
            except:
                return False

        elif word in self.console.ctx.dis.labels:
            ad = self.console.ctx.dis.labels[word]

        else:
            self.console.ctx.entry = word
            if not init_entry_addr(self.console.ctx):
                return False
            ad = self.console.ctx.entry_addr

        self.cursor_x = 0

        if self.goto_address(ad, h, w):
            self.saved_stack.clear()
            self.stack.append(topush)
            return True

        ret = self.exec_disasm(ad, h)
        if ret:
            self.cursor_y = 0
            self.win_y = 0
            self.saved_stack.clear()
            self.stack.append(topush)
            self.goto_address(ad, h, w)
        return ret


    def __do_go_back(self, args, h, w):
        ad, x, mode, offset_y = args
        self.cursor_x = x

        if mode == MODE_DECOMPILE:
            self.win_y = offset_y[0]
            self.cursor_y = offset_y[1]
            if self.mode == MODE_DECOMPILE and ad == self.console.ctx.entry_addr:
                return True

            self.mode = mode
            ret = self.exec_disasm(ad, h)

        else:
            if self.mode == MODE_DUMP and self.goto_address(ad, h, w):
                if offset_y != 0:
                    self.scroll_up(h, offset_y, False)
                return True

            self.mode = mode
            ret = self.exec_disasm(ad, h)

            if ret:
                self.goto_address(ad, h, w)
                if offset_y != 0:
                    self.scroll_up(h, offset_y, False)

        return ret


    def main_cmd_escape(self, h, w):
        if not self.stack:
            return False
        poped = self.stack.pop(-1)
        self.saved_stack.append(self.__compute_curr_position())
        return self.__do_go_back(poped, h, w)


    def main_cmd_reenter(self, h, w):
        if not self.saved_stack:
            return False
        poped = self.saved_stack.pop(-1)
        self.stack.append(self.__compute_curr_position())
        return self.__do_go_back(poped, h, w)


    def main_cmd_switch_mode(self, h, w):
        # Get a line with an address: the cursor is maybe on a comment
        l = self.win_y + self.cursor_y
        while l not in self.output.line_addr and l <= len(self.token_lines):
            l += 1

        ad = self.output.line_addr[l]

        if self.mode == MODE_DUMP:
            func_id = self.dis.mem.get_func_id(ad)

            if func_id == -1:
                self.status_bar("not in a function: create a function or use "
                                "the cmd x in the console", h, True)
                return False

            ad_disasm = self.dis.func_id[func_id]

        else:
            ad_disasm = ad

        self.stack.append(self.__compute_curr_position())

        if self.mode == MODE_DUMP:
            self.mode = MODE_DECOMPILE
        else:
            self.mode = MODE_DUMP

        ret = self.exec_disasm(ad_disasm, h)

        if ret:
            self.cursor_x = 0
            self.win_y = 0
            self.cursor_y = 0
            self.goto_address(ad, h, w)

        return ret


    def reload_dump(self):
        self.console.ctx.entry_addr = self.first_addr
        self.console.ctx.dump = True
        o = self.dis.dump_asm(self.console.ctx, until=self.last_addr)
        self.console.ctx.dump = False
        self.output = o
        self.token_lines = o.token_lines


    def main_cmd_code(self, h, w):
        if self.mode == MODE_DECOMPILE:
            return False

        line = self.win_y + self.cursor_y
        if line not in self.output.line_addr:
            return False

        ad = self.output.line_addr[line]

        if self.dis.mem.is_code(ad):
            return False

        self.console.analyzer.msg.put((ad, False, self.queue_wait_analyzer))
        self.queue_wait_analyzer.get()
        self.reload_dump()
        self.console.ctx.db.modified = True
        return True


    def main_cmd_set_function(self, h, w):
        if self.mode == MODE_DECOMPILE:
            return False

        line = self.win_y + self.cursor_y
        if line not in self.output.line_addr:
            return False

        # TODO: check if the address is not already in a function

        ad = self.output.line_addr[line]
        self.console.analyzer.msg.put((ad, True, self.queue_wait_analyzer))
        self.queue_wait_analyzer.get()
        self.reload_dump()
        self.console.ctx.db.modified = True
        self.goto_address(ad, h, w)
        return True


    def main_cmd_xrefs(self, h, w):
        word = self.get_word_under_cursor()
        if word is None:
            return False

        if word.startswith("0x"):
            try:
                ad = int(word, 16)
            except:
                return False

        elif word in self.console.ctx.dis.labels:
            ad = self.console.ctx.dis.labels[word]

        else:
            self.console.ctx.entry = word
            if not init_entry_addr(self.console.ctx):
                return False
            ad = self.console.ctx.entry_addr

        if ad not in self.dis.xrefs:
            self.status_bar("no xrefs", h, True)
            return False

        h2 = int(h*3/4)
        w2 = int(w*6/7)

        x = int((w - w2)/2) - 1
        y = int((h - h2)/2) - 1

        # A background with borders
        scr_borders = curses.newwin(h2 + 2, w2 + 2, y, x)
        scr_borders.border()
        title = " xrefs for " + hex(ad)
        scr_borders.addstr(0, int((w2 - len(title))/2), title)
        scr_borders.refresh()

        # The message box with xrefs
        o = self.dis.dump_xrefs(self.console.ctx, ad)
        scr = curses.newwin(h2, w2, y + 1, x + 1)
        w = Window(o)
        ret = w.start_view(scr)

        if not ret:
            return True

        # Goto the selected xref

        ad = o.line_addr[w.win_y + w.cursor_y]
        topush = self.__compute_curr_position()
        self.cursor_x = 0

        if self.goto_address(ad, h, w):
            self.saved_stack.clear()
            self.stack.append(topush)
            return True

        ad_disasm = ad

        if self.mode == MODE_DECOMPILE:
            func_id = self.dis.mem.get_func_id(ad)
            if func_id == -1:
                self.mode = MODE_DUMP
            else:
                ad_disasm = self.dis.func_id[func_id]

        ret = self.exec_disasm(ad_disasm, h)
        if ret:
            self.cursor_y = 0
            self.win_y = 0
            self.saved_stack.clear()
            self.stack.append(topush)
            self.goto_address(ad, h, w)
        return ret


    def mouse_double_left_click(self, h, w):
        return self.main_cmd_enter(h, w)


    # Inline comment editor : keys mapping

    def inline_k_left(self, xbegin, i, w):
        if i != 0:
            i -= 1
            self.cursor_x -= 1
        return i

    def inline_k_right(self, xbegin, i, w):
        if i != len(self.comm):
            i += 1
            self.cursor_x += 1
            # TODO: fix self.cursor_x >= w
            if self.cursor_x >= w:
                i -= self.cursor_x - w + 1
                self.cursor_x = w - 1
        return i

    def inline_k_backspace(self, xbegin, i, w):
        if i != 0:
            del self.comm[i-1]
            i -= 1
            self.cursor_x -= 1
        return i

    def inline_k_home(self, xbegin, i, w):
        self.cursor_x = xbegin + 3 # 3 for " ; "
        return 0

    def inline_k_end(self, xbegin, i, w):
        n = len(self.comm)
        self.cursor_x = xbegin + 3 + n
        i = n
        # TODO: fix self.cursor_x >= w
        if self.cursor_x >= w:
            i -= self.cursor_x - w + 1
            self.cursor_x = w - 1
        return i

    def inline_k_delete(self, xbegin, i, w):
        if i != len(self.comm):
            del self.comm[i]
        return i

    def inline_k_enter(self, xbegin, i, w):
        line = self.win_y + self.cursor_y
        addr = self.output.line_addr[line]
        lines = self.output.lines

        xbegin, idx_token = self.output.index_end_inst[line]

        if addr in self.dis.user_inline_comments:
            is_new_comment = False
            # Extract the comment which starts by #
            instr_comm = lines[line][xbegin + 3 + len(self.comm) + 1:]
        else:
            is_new_comment = True
            instr_comm = lines[line][xbegin + 1:]

        if not self.comm:
            if not is_new_comment:
                del self.dis.user_inline_comments[addr]

                lines[line] = "".join([
                    lines[line][:xbegin],
                    " ",
                    instr_comm])

                # Remove space and comment
                del self.token_lines[line][idx_token]
                del self.token_lines[line][idx_token]
        else:
            self.comm = "".join(self.comm)
            self.dis.user_inline_comments[addr] = self.comm

            if is_new_comment:
                # Space token
                self.token_lines[line].insert(idx_token,
                        (" ", 0, False))

                # Insert the new token
                self.token_lines[line].insert(idx_token + 1,
                        ("; " + self.comm, COLOR_USER_COMMENT.val, False))

                self.output.index_end_inst[line] = \
                        (xbegin, idx_token)
            else:
                # Plus 1 for the space token
                self.token_lines[line][idx_token + 1] = \
                        ("; " + self.comm, COLOR_USER_COMMENT.val, False)

            lines[line] = "".join([
                lines[line][:xbegin],
                " ; ",
                self.comm,
                " ",
                instr_comm])
        return i

    def inline_k_ctrl_u(self, xbegin, i, w):
        self.comm = self.comm[i:]
        self.cursor_x = xbegin + 3 # 3 for " ; "
        return 0

    def inline_k_ctrl_k(self, xbegin, i, w):
        self.comm = self.comm[:i]
        return i
