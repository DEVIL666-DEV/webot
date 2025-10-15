import discord
from discord.ext import commands
import sqlite3
from datetime import datetime
import re
import csv
import io
from io import BytesIO
import os
from .attendance import SessionSelectView

try:
    import matplotlib.pyplot as plt
    import matplotlib.font_manager as fm
    import arabic_reshaper
    from bidi.algorithm import get_display
    
    # Load Unifont if available
    font_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fonts")
    unifont_path = os.path.join(font_dir, "unifont-16.0.04.otf")
    if os.path.exists(unifont_path):
        fm.fontManager.addfont(unifont_path)
    
    # Simple font configuration
    plt.rcParams['font.sans-serif'] = ['Unifont', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False
    
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    print("Matplotlib not available - using text attendance reports only")

FC_LEVEL_MAPPING = {
    31: "30-1", 32: "30-2", 33: "30-3", 34: "30-4",
    35: "FC 1", 36: "FC 1-1", 37: "FC 1-2", 38: "FC 1-3", 39: "FC 1-4",
    40: "FC 2", 41: "FC 2-1", 42: "FC 2-2", 43: "FC 2-3", 44: "FC 2-4",
    45: "FC 3", 46: "FC 3-1", 47: "FC 3-2", 48: "FC 3-3", 49: "FC 3-4",
    50: "FC 4", 51: "FC 4-1", 52: "FC 4-2", 53: "FC 4-3", 54: "FC 4-4",
    55: "FC 5", 56: "FC 5-1", 57: "FC 5-2", 58: "FC 5-3", 59: "FC 5-4",
    60: "FC 6", 61: "FC 6-1", 62: "FC 6-2", 63: "FC 6-3", 64: "FC 6-4",
    65: "FC 7", 66: "FC 7-1", 67: "FC 7-2", 68: "FC 7-3", 69: "FC 7-4",
    70: "FC 8", 71: "FC 8-1", 72: "FC 8-2", 73: "FC 8-3", 74: "FC 8-4",
    75: "FC 9", 76: "FC 9-1", 77: "FC 9-2", 78: "FC 9-3", 79: "FC 9-4",
    80: "FC 10", 81: "FC 10-1", 82: "FC 10-2", 83: "FC 10-3", 84: "FC 10-4"
}

EVENT_TYPE_ICONS = {
    "Foundry": "🏭",
    "Canyon Clash": "⚔️",
    "Crazy Joe": "🤪",
    "Bear Trap": "🐻",
    "Castle Battle": "🏰",
    "Frostdragon Tyrant": "🐉",
    "Other": "📋"
}

class ExportFormatSelectView(discord.ui.View):
    def __init__(self, cog, records, session_info):
        super().__init__(timeout=300)
        self.cog = cog
        self.records = records
        self.session_info = session_info

    @discord.ui.select(
        placeholder="Select export format...",
        options=[
            discord.SelectOption(label="CSV", value="csv", description="Comma-separated values", emoji="📄"),
            discord.SelectOption(label="TSV", value="tsv", description="Tab-separated values", emoji="📋"),
            discord.SelectOption(label="HTML", value="html", description="Web page format", emoji="🌐")
        ]
    )
    async def format_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        await self.cog.process_export(interaction, select.values[0], self.records, self.session_info)

class ChannelSelectView(discord.ui.View):
    def __init__(self, cog, embeds, image_file=None):
        super().__init__(timeout=300)
        self.cog = cog
        self.embeds = embeds
        self.image_file = image_file

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="Select channel to post report...",
        channel_types=[discord.ChannelType.text]
    )
    async def channel_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        await self.cog.post_report_to_channel(interaction, select.values[0], self.embeds, self.image_file)

class AttendanceReport(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _get_status_emoji(self, status):
        """Helper to get status emoji"""
        return {"present": "✅", "absent": "❌", "not_recorded": "⚪"}.get(status, "❓")

    def _format_last_attendance(self, last_attendance):
        """Helper to format last attendance with emojis"""
        if last_attendance == "N/A" or "(" not in last_attendance:
            return last_attendance

        replacements = [
            ("present", "✅"), ("Present", "✅"),
            ("absent", "❌"), ("Absent", "❌"),
            ("not_recorded", "⚪"), ("Not Recorded", "⚪"), ("not recorded", "⚪")
        ]

        for old, new in replacements:
            last_attendance = last_attendance.replace(old, new)
        return last_attendance

    def _fix_arabic_text(self, text):
        """Fix Arabic text for proper display in text reports"""
        if not text:
            return text
        if re.search(r'[\u0600-\u06FF]', text):
            try:
                import arabic_reshaper
                from bidi.algorithm import get_display
                reshaped = arabic_reshaper.reshape(text)
                display_text = get_display(reshaped)
                # Use LEFT-TO-RIGHT MARK to force LTR context
                return f'\u200E{display_text}\u200E'
            except:
                return text
        return text
    
    def _format_date_for_table(self, date_str: str) -> str:
        """Format date string for table display"""
        try:
            if 'T' in date_str:
                # Parse ISO format and convert to desired format
                date_obj = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                return date_obj.strftime("%Y-%m-%d %H:%M UTC")
            else: # Already formatted or partial date
                return date_str
        except:
            # Fallback to original if parsing fails
            return date_str.split()[0] if date_str else "N/A"

    def _create_error_embed(self, title, description, color=discord.Color.red()):
        """Helper to create error embeds"""
        return discord.Embed(title=title, description=description, color=color)

    def _create_back_view(self, callback):
        """Helper to create back button view"""
        view = discord.ui.View()
        back_button = discord.ui.Button(label="⬅️ Back", style=discord.ButtonStyle.secondary)
        back_button.callback = callback
        view.add_item(back_button)
        return view

    async def _get_alliance_name(self, alliance_id):
        """Helper to get alliance name"""
        with sqlite3.connect('db/alliance.sqlite') as db:
            cursor = db.cursor()
            cursor.execute("SELECT name FROM alliance_list WHERE alliance_id = ?", (alliance_id,))
            result = cursor.fetchone()
            return result[0] if result else "Unknown Alliance"

    async def get_user_report_preference(self, user_id):
        """Get user's report preference"""
        try:
            with sqlite3.connect('db/attendance.sqlite') as db:
                cursor = db.cursor()
                cursor.execute("""
                    SELECT report_type FROM user_preferences
                    WHERE user_id = ?
                """, (user_id,))
                result = cursor.fetchone()
                return result[0] if result else "text"
        except Exception:
            return "text"

    async def get_user_sort_preference(self, user_id):
        """Get user's sort preference"""
        try:
            with sqlite3.connect('db/attendance.sqlite') as db:
                cursor = db.cursor()
                cursor.execute("""
                    SELECT sort_preference FROM user_preferences
                    WHERE user_id = ?
                """, (user_id,))
                result = cursor.fetchone()
                return result[0] if result else "points_desc"
        except Exception:
            return "points_desc"

    async def set_user_sort_preference(self, user_id, sort_type):
        """Set user's sort preference"""
        try:
            with sqlite3.connect('db/attendance.sqlite') as db:
                cursor = db.cursor()
                cursor.execute("SELECT user_id FROM user_preferences WHERE user_id = ?", (user_id,))
                exists = cursor.fetchone()

                if exists:
                    cursor.execute("""
                        UPDATE user_preferences
                        SET sort_preference = ?
                        WHERE user_id = ?
                    """, (sort_type, user_id))
                else:
                    cursor.execute("""
                        INSERT INTO user_preferences (user_id, sort_preference)
                        VALUES (?, ?)
                    """, (user_id, sort_type))

                db.commit()
                return True
        except Exception as e:
            print(f"Error setting sort preference: {e}")
            return False

    def _get_sort_function(self, sort_preference):
        """Get sorting function based on user preference"""
        if sort_preference == "name_asc":
            def sort_key(record):
                attendance_type = record[2]
                nickname = record[1] or "Unknown"

                import unicodedata
                import re

                sortable_name = re.sub(r'[༺༻༈◈彡ミ~\{\}:\[\]]+', '', nickname)
                sortable_name = ' '.join(sortable_name.split())
                sortable_name = unicodedata.normalize('NFC', sortable_name).lower()

                type_priority = {"present": 1, "absent": 2}.get(attendance_type, 3)
                return (type_priority, sortable_name)
            return sort_key

        elif sort_preference == "name_asc_all":
            def sort_key(record):
                nickname = record[1] or "Unknown"

                import unicodedata
                import re

                sortable_name = re.sub(r'[༺༻༈◈彡ミ~\{\}:\[\]]+', '', nickname)
                sortable_name = ' '.join(sortable_name.split())
                sortable_name = unicodedata.normalize('NFC', sortable_name).lower()

                return sortable_name
            return sort_key

        elif sort_preference == "last_attended_first":
            def sort_key(record):
                attendance_type = record[2]
                last_attendance = record[4] or "N/A"
                points = record[3] or 0

                current_present = (attendance_type == "present")

                # Determine last attendance status
                if "Present" in last_attendance:
                    last_status = "present"
                elif "Absent" in last_attendance:
                    last_status = "absent"
                else:
                    last_status = "not_recorded"

                # Priority groups
                if current_present:
                    if last_status == "present":
                        priority = 1
                    elif last_status == "absent":
                        priority = 2
                    else:
                        priority = 3
                else:
                    if last_status == "present":
                        priority = 4
                    elif last_status == "absent":
                        priority = 5
                    else:
                        priority = 6

                return (priority, -points)
            return sort_key

        else:
            def sort_key(record):
                attendance_type = record[2]
                points = record[3] or 0
                type_priority = {"present": 1, "absent": 2}.get(attendance_type, 3)
                return (type_priority, -points)
            return sort_key

    async def generate_csv_export(self, records, session_info):
        """Generate CSV export file"""
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Write metadata
        writer.writerow(['Session Name:', session_info['session_name']])
        writer.writerow(['Alliance:', session_info['alliance_name']])
        writer.writerow(['Event Type:', session_info.get('event_type', 'Other')])
        if session_info.get('event_date'):
            writer.writerow(['Event Date:', session_info['event_date'].split('T')[0] if isinstance(session_info['event_date'], str) else session_info['event_date']])
        writer.writerow(['Export Date:', datetime.now().strftime('%Y-%m-%d %H:%M:%S')])
        writer.writerow(['Total Players:', session_info['total_players']])
        writer.writerow(['Present:', session_info['present_count'], 'Absent:', session_info['absent_count']])
        writer.writerow([])
        
        # Write headers
        writer.writerow(['ID', 'Nickname', 'Status', 'Points', 'Last Event Attendance', 'Marked By'])
        
        # Write data
        for record in records:
            writer.writerow([
                record[0],  # ID
                record[1],  # Nickname
                record[2].replace('_', ' ').title(),  # Status
                record[3] if record[3] else 0,  # Points
                record[4] if record[4] else 'N/A',  # Last Event
                record[6]   # Marked By
            ])
        
        output.seek(0)
        filename = f"attendance_{session_info['alliance_name'].replace(' ', '_')}_{session_info['session_name'].replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        return discord.File(io.BytesIO(output.getvalue().encode('utf-8')), filename=filename)

    async def generate_tsv_export(self, records, session_info):
        """Generate TSV export file"""
        output = io.StringIO()
        writer = csv.writer(output, delimiter='\t')
        
        # Write metadata
        writer.writerow(['Session Name:', session_info['session_name']])
        writer.writerow(['Alliance:', session_info['alliance_name']])
        writer.writerow(['Event Type:', session_info.get('event_type', 'Other')])
        if session_info.get('event_date'):
            writer.writerow(['Event Date:', session_info['event_date'].split('T')[0] if isinstance(session_info['event_date'], str) else session_info['event_date']])
        writer.writerow(['Export Date:', datetime.now().strftime('%Y-%m-%d %H:%M:%S')])
        writer.writerow(['Total Players:', session_info['total_players']])
        writer.writerow(['Present:', session_info['present_count'], 'Absent:', session_info['absent_count']])
        writer.writerow([])  # Empty row
        
        # Write headers
        writer.writerow(['ID', 'Nickname', 'Status', 'Points', 'Last Event Attendance', 'Marked By'])
        
        # Write data
        for record in records:
            writer.writerow([
                record[0],  # ID
                record[1],  # Nickname
                record[2].replace('_', ' ').title(),  # Status
                record[3] if record[3] else 0,  # Points
                record[4] if record[4] else 'N/A',  # Last Event
                record[6]   # Marked By
            ])
        
        output.seek(0)
        filename = f"attendance_{session_info['alliance_name'].replace(' ', '_')}_{session_info['session_name'].replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.tsv"
        return discord.File(io.BytesIO(output.getvalue().encode('utf-8')), filename=filename)

    async def generate_html_export(self, records, session_info):
        """Generate HTML export file"""
        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Attendance Report - {session_info['alliance_name']} - {session_info['session_name']}</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            margin: 20px;
            background-color: #f5f5f5;
        }}
        .header {{
            background-color: #4CAF50;
            color: white;
            padding: 20px;
            border-radius: 5px;
            margin-bottom: 20px;
        }}
        .stats {{
            background-color: white;
            padding: 15px;
            border-radius: 5px;
            margin-bottom: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        table {{
            border-collapse: collapse;
            width: 100%;
            background-color: white;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        th, td {{
            border: 1px solid #ddd;
            padding: 12px;
            text-align: left;
        }}
        th {{
            background-color: #4CAF50;
            color: white;
            font-weight: bold;
        }}
        tr:nth-child(even) {{
            background-color: #f9f9f9;
        }}
        tr:hover {{
            background-color: #f5f5f5;
        }}
        .present {{ color: #4CAF50; font-weight: bold; }}
        .absent {{ color: #f44336; font-weight: bold; }}
        .not-recorded {{ color: #9e9e9e; font-weight: bold; }}
        .footer {{
            text-align: center;
            margin-top: 20px;
            color: #666;
            font-size: 12px;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>Attendance Report</h1>
        <h2>{session_info['alliance_name']} - {session_info['session_name']}</h2>
    </div>
    
    <div class="stats">
        <h3>Summary</h3>
        <p><strong>Event Type:</strong> {session_info.get('event_type', 'Other')}</p>
        {'<p><strong>Event Date:</strong> ' + (session_info['event_date'].split('T')[0] if isinstance(session_info.get('event_date'), str) else str(session_info.get('event_date', ''))) + '</p>' if session_info.get('event_date') else ''}
        <p><strong>Export Date:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        <p><strong>Total Players:</strong> {session_info['total_players']}</p>
        <p>
            <span class="present">Present: {session_info['present_count']}</span> | 
            <span class="absent">Absent: {session_info['absent_count']}</span>
        </p>
    </div>
    
    <table>
        <thead>
            <tr>
                <th>ID</th>
                <th>Nickname</th>
                <th>Status</th>
                <th>Points</th>
                <th>Last Event Attendance</th>
                <th>Marked By</th>
            </tr>
        </thead>
        <tbody>
"""
        
        # Add data rows
        for record in records:
            status = record[2]
            status_class = status.replace('_', '-')
            status_display = status.replace('_', ' ').title()
            
            html_content += f"""            <tr>
                <td>{record[0]}</td>
                <td>{record[1]}</td>
                <td class="{status_class}">{status_display}</td>
                <td>{record[3] if record[3] else 0:,}</td>
                <td>{record[4] if record[4] else 'N/A'}</td>
                <td>{record[6]}</td>
            </tr>
"""
        
        html_content += """        </tbody>
    </table>
    
    <div class="footer">
        <p>Generated by Whiteout Discord Bot</p>
    </div>
</body>
</html>"""
        
        filename = f"attendance_{session_info['alliance_name'].replace(' ', '_')}_{session_info['session_name'].replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        return discord.File(io.BytesIO(html_content.encode('utf-8')), filename=filename)

    async def post_report_to_channel(self, interaction: discord.Interaction, channel, embeds, image_file=None):
        """Post attendance report to a selected channel"""
        try:
            await interaction.response.defer(ephemeral=True)

            # Resolve the channel object (from AppCommandChannel to actual Channel)
            actual_channel = interaction.guild.get_channel(channel.id)
            if not actual_channel:
                await interaction.followup.send(
                    "❌ Could not access that channel.",
                    ephemeral=True
                )
                return

            # Check permissions
            if not actual_channel.permissions_for(interaction.guild.me).send_messages:
                await interaction.followup.send(
                    "❌ I don't have permission to send messages in that channel.",
                    ephemeral=True
                )
                return

            if not actual_channel.permissions_for(interaction.user).send_messages:
                await interaction.followup.send(
                    "❌ You don't have permission to send messages in that channel.",
                    ephemeral=True
                )
                return

            # Post the report embeds to the channel
            if image_file:
                # For matplotlib reports with image
                # Need to recreate the file since it may have been consumed
                if hasattr(image_file, 'fp'):
                    image_file.fp.seek(0)
                await actual_channel.send(embed=embeds[0], file=image_file)

                # Post additional embeds if any (shouldn't be for matplotlib)
                for embed in embeds[1:]:
                    await actual_channel.send(embed=embed)
            else:
                # For text reports
                for embed in embeds:
                    await actual_channel.send(embed=embed)

            await interaction.followup.send(
                f"✅ Attendance report posted to {actual_channel.mention}!",
                ephemeral=True
            )

        except discord.Forbidden:
            await interaction.followup.send(
                "❌ I don't have permission to post in that channel.",
                ephemeral=True
            )
        except Exception as e:
            print(f"Error posting report to channel: {e}")
            await interaction.followup.send(
                "❌ An error occurred while posting the report.",
                ephemeral=True
            )

    async def process_export(self, interaction: discord.Interaction, format_type: str, records, session_info):
        """Process export request and send file via DM"""
        try:
            # Defer the response as file generation might take a moment
            await interaction.response.defer(ephemeral=True)
            
            # Generate the appropriate file
            if format_type == "csv":
                file = await self.generate_csv_export(records, session_info)
                format_name = "CSV"
            elif format_type == "tsv":
                file = await self.generate_tsv_export(records, session_info)
                format_name = "TSV"
            elif format_type == "html":
                file = await self.generate_html_export(records, session_info)
                format_name = "HTML"
            else:
                await interaction.followup.send(
                    "❌ Invalid export format selected.",
                    ephemeral=True
                )
                return
            
            # Try to DM the file
            try:
                await interaction.user.send(
                    f"📊 **Attendance Report Export**\n"
                    f"**Format:** {format_name}\n"
                    f"**Alliance:** {session_info['alliance_name']}\n"
                    f"**Session:** {session_info['session_name']}\n"
                    f"**Event Type:** {session_info.get('event_type', 'Other')}\n"
                    f"**Total Records:** {session_info['total_players']}",
                    file=file
                )
                await interaction.followup.send(
                    "✅ Attendance report sent to your DMs!",
                    ephemeral=True
                )
            except discord.Forbidden:
                await interaction.followup.send(
                    "❌ Could not send DM. Please enable DMs from server members and try again.",
                    ephemeral=True
                )
            except discord.HTTPException as e:
                if "Maximum message size" in str(e):
                    await interaction.followup.send(
                        "❌ Report too large to send via Discord (8MB limit). Please try exporting fewer records.",
                        ephemeral=True
                    )
                else:
                    await interaction.followup.send(
                        f"❌ An error occurred while sending the report: {str(e)}",
                        ephemeral=True
                    )
                    
        except Exception as e:
            print(f"Error in process_export: {e}")
            await interaction.followup.send(
                "❌ An error occurred while generating the export.",
                ephemeral=True
            )

    async def show_attendance_report(self, interaction: discord.Interaction, alliance_id: int, session_name: str, 
                                   is_preview=False, selected_players=None, session_id=None, marking_view=None):
        """
        Show attendance records with user's preferred format
        - is_preview=True: 3-column format for marking attendance preview
        - is_preview=False: 6-column format for full report viewing
        - selected_players: Used only for preview mode from marking flow
        """
        try:            
            # Get user's report preference
            report_type = await self.get_user_report_preference(interaction.user.id)
            
            # If matplotlib is not available, force text mode
            if report_type == "matplotlib" and not MATPLOTLIB_AVAILABLE:
                report_type = "text"
                
            if report_type == "matplotlib":
                await self.show_matplotlib_report(interaction, alliance_id, session_name, is_preview, selected_players, session_id, marking_view)
            else:
                await self.show_text_report(interaction, alliance_id, session_name, is_preview, selected_players, session_id, marking_view)
                
        except Exception as e:
            print(f"Error showing attendance report: {e}")
            import traceback
            traceback.print_exc()
            await interaction.edit_original_response(
                content="❌ An error occurred while generating attendance report.",
                embed=None,
                view=None
            )

    async def show_matplotlib_report(self, interaction: discord.Interaction, alliance_id: int, session_name: str,
                                   is_preview=False, selected_players=None, session_id=None, marking_view=None):
        """Show attendance records as a Matplotlib table image"""
        try:
            # Get alliance name
            alliance_name = await self._get_alliance_name(alliance_id)

            # Handle preview mode vs full report mode
            if is_preview and selected_players:
                # Preview mode - use selected_players data
                filtered_players = {
                    fid: data for fid, data in selected_players.items()
                    if data['attendance_type'] != 'not_recorded'
                }
                
                # Get event info from marking view if available
                event_type = marking_view.event_type if marking_view and hasattr(marking_view, 'event_type') else None
                event_date = marking_view.event_date if marking_view and hasattr(marking_view, 'event_date') else None
                
                # Convert to records format for consistency
                records = []
                for fid, data in sorted(filtered_players.items(), key=lambda x: x[1]['points'], reverse=True):
                    records.append((
                        fid,
                        data['nickname'],
                        data['attendance_type'],
                        data['points'],
                        data.get('last_event_attendance', 'N/A'),
                        None,  # No date in preview
                        None   # No marked_by in preview
                    ))
                
                if not records:
                    await interaction.response.edit_message(
                        content=f"❌ No attendance has been marked yet.",
                        embed=None,
                        view=None
                    )
                    return
                    
                present_count = sum(1 for r in records if r[2] == 'present')
                absent_count = sum(1 for r in records if r[2] == 'absent')
                
            else:
                # Full report mode - fetch from database
                records = []
                event_type = None
                event_date = None
                with sqlite3.connect('db/attendance.sqlite') as attendance_db:
                    cursor = attendance_db.cursor()
                    if session_id:
                        # Use session_id if provided (more specific)
                        cursor.execute("""
                            SELECT player_id, player_name, status, points, event_type, event_date, marked_by_username
                            FROM attendance_records
                            WHERE session_id = ? AND status != 'not_recorded'
                            ORDER BY points DESC, marked_at DESC
                        """, (session_id,))
                    else:
                        # Fallback to session_name (less specific, may include multiple sessions)
                        cursor.execute("""
                            SELECT player_id, player_name, status, points, event_type, event_date, marked_by_username
                            FROM attendance_records
                            WHERE alliance_id = ? AND session_name = ? AND status != 'not_recorded'
                            ORDER BY points DESC, marked_at DESC
                        """, (str(alliance_id), session_name))
                    db_records = cursor.fetchall()

                    # Get session_id if not provided (needed for last event lookup)
                    if not session_id and db_records:
                        cursor.execute("""
                            SELECT DISTINCT session_id FROM attendance_records
                            WHERE alliance_id = ? AND session_name = ?
                            LIMIT 1
                        """, (str(alliance_id), session_name))
                        result = cursor.fetchone()
                        if result:
                            session_id = result[0]
                    
                    # Convert to expected format and get event info
                    for record in db_records:
                        if event_type is None:
                            event_type = record[4]
                        if event_date is None:
                            event_date = record[5]
                        
                        # Fetch last event attendance for this player
                        last_event_attendance = await self.fetch_last_event_attendance(
                            record[0], event_type, event_date, session_id
                        ) if event_type and event_date and session_id else "N/A"
                        
                        # Format: (id, nickname, status, points, last_event_attendance, marked_date, marked_by)
                        records.append((
                            record[0],  # player_id
                            record[1],  # player_name
                            record[2],  # status
                            record[3],  # points
                            last_event_attendance,
                            event_date, # use event_date
                            record[6]   # marked_by_username
                        ))

                if not records:
                    await interaction.response.edit_message(
                        content=f"❌ No attendance records found for session '{session_name}' in {alliance_name}.",
                        embed=None,
                        view=None
                    )
                    return

                # Count attendance types
                present_count = sum(1 for r in records if r[2] == 'present')
                absent_count = sum(1 for r in records if r[2] == 'absent')

            not_recorded_count = 0  # We're not showing not_recorded in reports

            # Apply user's sort preference
            sort_preference = await self.get_user_sort_preference(interaction.user.id)
            sort_key = self._get_sort_function(sort_preference)
            records = sorted(records, key=sort_key)

            # Generate Matplotlib table image - different headers for preview vs full
            if is_preview:
                headers = ["Player", "Status", "Points"]
                table_color = '#28a745'  # Green for preview
            else:
                headers = ["Player", "Status", "Last Event", "Points", "Marked By"]
                table_color = '#1f77b4'  # Blue for full report
            table_data = []
            
            def fix_arabic(text):
                if text and re.search(r'[\u0600-\u06FF]', text):
                    try:
                        reshaped = arabic_reshaper.reshape(text)
                        return get_display(reshaped)
                    except Exception:
                        return text
                return text
                
            def wrap_text(text, width=20):
                if not text:
                    return ""
                lines = []
                for part in str(text).split('\n'):
                    while len(part) > width:
                        lines.append(part[:width])
                        part = part[width:]
                    lines.append(part)
                return '\n'.join(lines)

            for row in records:
                if is_preview:
                    # Preview mode - 3 columns
                    status_display = {
                        "present": "Present",
                        "absent": "Absent"
                    }.get(row[2], row[2])
                    
                    table_data.append([
                        wrap_text(fix_arabic(row[1] or "Unknown")),
                        wrap_text(fix_arabic(status_display)),
                        wrap_text(f"{row[3]:,}" if row[3] else "0")
                    ])
                else:
                    # Full report - 5 columns (Date column removed)
                    table_data.append([
                        wrap_text(fix_arabic(row[1] or "Unknown")),
                        wrap_text(fix_arabic(row[2].replace('_', ' ').title())),
                        wrap_text(fix_arabic(row[4] if row[4] else "N/A"), width=40),
                        wrap_text(f"{row[3]:,}" if row[3] else "0"),
                        wrap_text(fix_arabic(row[6] or "Unknown"))
                    ])

            # Calculate figure height based on number of rows
            row_height = 0.6 if not is_preview else 0.5
            header_height = 2
            fig_height = min(header_height + len(table_data) * row_height, 25 if not is_preview else 20)
            
            # Adjust figure width for preview mode
            fig_width = 10 if is_preview else 13
            
            fig, ax = plt.subplots(figsize=(fig_width, fig_height))
            ax.axis('off')
            
            # Format title with event type and date
            title_text = f'Attendance Report - {alliance_name} | {session_name}'
            if event_type:
                title_text += f' [{event_type}]'
            if event_date:
                if isinstance(event_date, str):
                    date_str = event_date.split('T')[0] if 'T' in event_date else event_date.split()[0]
                else:
                    try:
                        date_str = event_date.strftime("%Y-%m-%d")
                    except:
                        date_str = str(event_date)
                title_text += f' | Date: {date_str}'
            
            ax.text(0.5, 0.98, title_text, 
                   transform=ax.transAxes, fontsize=16 if not is_preview else 14, color=table_color, 
                   ha='center', va='top', weight='bold')
            
            # Create table with adjusted position to avoid title overlap
            table = ax.table(
                cellText=table_data,
                colLabels=headers,
                cellLoc='left',
                loc='upper center',
                bbox=[0, -0.05, 1, 0.90],  # Move down and reduce height to avoid title
                colColours=[table_color]*len(headers)
            )
            table.auto_set_font_size(False)
            table.set_fontsize(12)
            table.scale(1, 1.5)
            
            # Set larger width for columns - only for full report
            if not is_preview:
                nrows = len(table_data) + 1
                for row in range(nrows):
                    cell = table[(row, 2)]
                    cell.set_width(0.35)
                    cell = table[(row, 4)]
                    cell.set_width(0.25)

            img_buffer = BytesIO()
            plt.savefig(img_buffer, format='png', bbox_inches='tight')
            plt.close(fig)
            img_buffer.seek(0)

            file = discord.File(img_buffer, filename="attendance_report.png")

            # Format embed title with event type
            embed_title = f"📊 Attendance Report - {alliance_name}"
            description_text = f"**Session:** {session_name}"
            if event_type:
                description_text += f" [{event_type}]"
            description_text += f"\n**Total Marked:** {len(records)} players"
            
            embed = discord.Embed(
                title=embed_title,
                description=description_text,
                color=discord.Color.green() if is_preview else discord.Color.blue()
            )
            embed.set_image(url="attachment://attendance_report.png")

            # Create view based on mode
            if is_preview:
                # Preview mode - create a simple back button that clears attachments
                view = discord.ui.View(timeout=7200)
                back_button = discord.ui.Button(
                    label="⬅️ Back",
                    style=discord.ButtonStyle.secondary
                )
                
                async def preview_back_callback(back_interaction: discord.Interaction):
                    # Check if we have a stored marking view to return to
                    if marking_view:
                        await marking_view.update_main_embed(back_interaction)
                    else:
                        # Fallback - just remove attachment
                        await back_interaction.response.edit_message(attachments=[])
                
                back_button.callback = preview_back_callback
                view.add_item(back_button)
            else:
                # Full report mode - back and export buttons
                view = discord.ui.View(timeout=7200)
                
                # Back button
                back_button = discord.ui.Button(
                    label="⬅️ Back",
                    style=discord.ButtonStyle.secondary
                )
                
                async def back_callback(back_interaction: discord.Interaction):
                    await self.show_session_selection(back_interaction, alliance_id)
                
                back_button.callback = back_callback
                view.add_item(back_button)
                
                # Export button - only for full reports
                export_button = discord.ui.Button(
                    label="Export",
                    emoji="📥",
                    style=discord.ButtonStyle.primary
                )
                
                async def export_callback(export_interaction: discord.Interaction):
                    session_info = {
                        'session_name': session_name,
                        'alliance_name': alliance_name,
                        'event_type': event_type or 'Other',
                        'event_date': event_date,
                        'total_players': len(records),
                        'present_count': present_count,
                        'absent_count': absent_count,
                        'not_recorded_count': not_recorded_count
                    }
                    export_view = ExportFormatSelectView(self, records, session_info)
                    await export_interaction.response.send_message(
                        "Select export format:",
                        view=export_view,
                        ephemeral=True
                    )
                
                export_button.callback = export_callback
                view.add_item(export_button)

                # Post to Channel button - only for full reports
                post_button = discord.ui.Button(
                    label="Post to Channel",
                    emoji="📢",
                    style=discord.ButtonStyle.success
                )

                async def post_callback(post_interaction: discord.Interaction):
                    # Create a fresh file for posting
                    img_buffer_copy = BytesIO()
                    img_buffer.seek(0)
                    img_buffer_copy.write(img_buffer.read())
                    img_buffer_copy.seek(0)
                    file_for_channel = discord.File(img_buffer_copy, filename="attendance_report.png")

                    channel_view = ChannelSelectView(self, [embed], image_file=file_for_channel)
                    await post_interaction.response.send_message(
                        "Select a channel to post the attendance report:",
                        view=channel_view,
                        ephemeral=True
                    )

                post_button.callback = post_callback
                view.add_item(post_button)

            # Handle both regular and deferred interactions
            if interaction.response.is_done():
                await interaction.edit_original_response(embed=embed, view=view, attachments=[file])
            else:
                await interaction.response.edit_message(embed=embed, view=view, attachments=[file])

        except Exception as e:
            print(f"Matplotlib error: {e}")
            # Fallback to text report
            await self.show_text_report(interaction, alliance_id, session_name, is_preview, selected_players, session_id, marking_view)

    async def fetch_last_event_attendance(self, player_id: str, event_type: str, event_date: str, session_id: str):
        """Fetch the last attendance for a player of the same event type before the current event date"""
        try:
            with sqlite3.connect('db/attendance.sqlite') as db:
                cursor = db.cursor()
                # Get the last attendance of the same event type before the current event date
                cursor.execute("""
                    SELECT status, event_date 
                    FROM attendance_records 
                    WHERE player_id = ? 
                    AND event_type = ? 
                    AND event_date < ?
                    AND session_id != ?
                    ORDER BY event_date DESC 
                    LIMIT 1
                """, (player_id, event_type, event_date, session_id))
                
                result = cursor.fetchone()
                if result:
                    status, last_date = result
                    # Format the date
                    try:
                        if isinstance(last_date, str):
                            last_date_obj = datetime.fromisoformat(last_date.replace('Z', '+00:00'))
                        else:
                            last_date_obj = last_date
                        date_str = last_date_obj.strftime("%m/%d")
                    except:
                        # Fallback for unparseable dates
                        if isinstance(last_date, str):
                            date_str = last_date.split('T')[0] if 'T' in last_date else last_date.split()[0] if ' ' in last_date else last_date
                        else:
                            date_str = str(last_date)
                    
                    status_display = status.replace('_', ' ').title()
                    return f"{status_display} ({date_str})"
                else:
                    # No record found - check if there are ANY previous events of this type
                    cursor.execute("""
                        SELECT COUNT(DISTINCT session_id) 
                        FROM attendance_records 
                        WHERE event_type = ? 
                        AND event_date < ?
                        AND session_id != ?
                    """, (event_type, event_date, session_id))
                    
                    event_count = cursor.fetchone()
                    if event_count and event_count[0] > 0:
                        # There were previous events of this type, but player wasn't in them
                        # This could mean they're new to the alliance
                        return "New Player"
                    else:
                        # This is the first event of this type
                        return "First Event"
        except Exception as e:
            print(f"Error fetching last attendance: {e}")
            return "N/A"

    async def show_text_report(self, interaction: discord.Interaction, alliance_id: int, session_name: str,
                             is_preview=False, selected_players=None, session_id=None, marking_view=None):
        """Show attendance records for a specific session with emoji-based formatting"""
        try:
            # Get alliance name
            alliance_name = await self._get_alliance_name(alliance_id)

            # Handle preview mode vs full report mode
            if is_preview and selected_players:
                # Preview mode - use selected_players data
                filtered_players = {
                    fid: data for fid, data in selected_players.items()
                    if data['attendance_type'] != 'not_recorded'
                }
                
                # Get event info from marking view if available
                event_type = marking_view.event_type if marking_view and hasattr(marking_view, 'event_type') else None
                event_date = marking_view.event_date if marking_view and hasattr(marking_view, 'event_date') else None
                
                # Convert to records format for consistency
                records = []
                for fid, data in sorted(filtered_players.items(), key=lambda x: x[1]['points'], reverse=True):
                    records.append((
                        fid,
                        data['nickname'],
                        data['attendance_type'],
                        data['points'],
                        data.get('last_event_attendance', 'N/A'),
                        event_date,  # use event_date from marking view
                        None   # No marked_by in preview
                    ))
                
                if not records:
                    await interaction.response.edit_message(
                        content=f"❌ No attendance has been marked yet.",
                        embed=None,
                        view=None
                    )
                    return
            else:
                # Full report mode - fetch from database
                records = []
                event_type = None
                event_date = None
                with sqlite3.connect('db/attendance.sqlite') as attendance_db:
                    cursor = attendance_db.cursor()
                    if session_id:
                        # Use session_id if provided (more specific)
                        cursor.execute("""
                            SELECT player_id, player_name, status, points, event_type, event_date, marked_by_username
                            FROM attendance_records
                            WHERE session_id = ? AND status != 'not_recorded'
                            ORDER BY points DESC, marked_at DESC
                        """, (session_id,))
                    else:
                        # Fallback to session_name (less specific, may include multiple sessions)
                        cursor.execute("""
                            SELECT player_id, player_name, status, points, event_type, event_date, marked_by_username
                            FROM attendance_records
                            WHERE alliance_id = ? AND session_name = ? AND status != 'not_recorded'
                            ORDER BY points DESC, marked_at DESC
                        """, (str(alliance_id), session_name))
                    db_records = cursor.fetchall()

                    # Get session_id if not provided (needed for last event lookup)
                    if not session_id and db_records:
                        cursor.execute("""
                            SELECT DISTINCT session_id FROM attendance_records
                            WHERE alliance_id = ? AND session_name = ?
                            LIMIT 1
                        """, (str(alliance_id), session_name))
                        result = cursor.fetchone()
                        if result:
                            session_id = result[0]
                    
                    # Convert to expected format and get event info
                    for record in db_records:
                        if event_type is None:
                            event_type = record[4]
                        if event_date is None:
                            event_date = record[5]
                        
                        # Fetch last event attendance for this player
                        last_event_attendance = await self.fetch_last_event_attendance(
                            record[0], event_type, event_date, session_id
                        ) if event_type and event_date and session_id else "N/A"
                        
                        # Format: (id, nickname, status, points, last_event_attendance, marked_date, marked_by)
                        records.append((
                            record[0],  # player_id
                            record[1],  # player_name
                            record[2],  # status
                            record[3],  # points
                            last_event_attendance,
                            event_date, # use event_date
                            record[6]   # marked_by_username
                        ))

            if not records:
                await interaction.edit_original_response(
                    content=f"❌ No attendance records found for session '{session_name}' in {alliance_name}.",
                    embed=None,
                    view=None
                )
                return

            # Count attendance types
            present_count = sum(1 for r in records if r[2] == 'present')
            absent_count = sum(1 for r in records if r[2] == 'absent')
            not_recorded_count = 0  # We're not showing not_recorded in reports anymore

            # Get session ID from attendance records if not provided
            if not session_id:
                try:
                    with sqlite3.connect('db/attendance.sqlite') as attendance_db:
                        cursor = attendance_db.cursor()
                        cursor.execute("""
                            SELECT DISTINCT session_id FROM attendance_records
                            WHERE session_name = ? AND alliance_id = ?
                            LIMIT 1
                        """, (session_name, str(alliance_id)))
                        result = cursor.fetchone()
                        if result:
                            session_id = result[0]
                except:
                    pass

            # Build the report sections
            report_sections = []
            
            # Summary section
            report_sections.append("📊 **SUMMARY**")
            session_line = f"**Session:** {session_name}"
            if event_type:
                session_line += f" [{event_type}]"
            report_sections.append(session_line)
            report_sections.append(f"**Alliance:** {alliance_name}")
            date_str = "N/A"
            if records and records[0][5]:
                event_date_value = records[0][5]
                if isinstance(event_date_value, str):
                    # String format - extract date portion
                    date_str = event_date_value.split('T')[0] if 'T' in event_date_value else event_date_value.split()[0]
                else:
                    # Datetime object - format it
                    try:
                        date_str = event_date_value.strftime("%Y-%m-%d")
                    except:
                        date_str = str(event_date_value)
            report_sections.append(f"**Date:** {date_str}")
            report_sections.append(f"**Total Marked:** {len(records)} players")
            report_sections.append(f"**Present:** {present_count} | **Absent:** {absent_count}")
            if session_id:
                report_sections.append(f"**Session ID:** {session_id}")
            report_sections.append("")
            
            # Player details section
            report_sections.append("👥 **PLAYER DETAILS**")
            report_sections.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

            # Get user's sort preference and apply sorting
            sort_preference = await self.get_user_sort_preference(interaction.user.id)
            sort_key = self._get_sort_function(sort_preference)
            sorted_records = sorted(records, key=sort_key)


            # Format sort description for footer
            sort_descriptions = {
                "points_desc": "Sorted by Points (Highest to Lowest)",
                "name_asc": "Sorted by Name (A-Z)",
                "name_asc_all": "Sorted by Name (A-Z, All Users)",
                "last_attended_first": "Sorted by Last Attended (Most Recent First)"
            }
            sort_footer = sort_descriptions.get(sort_preference, "Sorted by Points (Highest to Lowest)")
            
            for record in sorted_records:
                fid = record[0]
                nickname = record[1] or "Unknown"
                attendance_status = record[2]
                points = record[3] or 0
                last_event_attendance = record[4] or "N/A"

                # Fix Arabic text for proper display
                display_nickname = self._fix_arabic_text(nickname)

                # Get status emoji
                status_emoji = self._get_status_emoji(attendance_status)

                # Convert last attendance status to relevant emoji
                last_event_display = self._format_last_attendance(last_event_attendance)

                points_display = f"{points:,}" if points > 0 else "0"

                player_line = f"{status_emoji} **{display_nickname}** (ID: {fid})"
                if points > 0:
                    player_line += f" | **{points_display}** points"
                if last_event_attendance != "N/A":
                    player_line += f" | Last: {last_event_display}"

                report_sections.append(player_line)

            # Discord embed description limit is 4096 characters, but Discord truncates the display earlier
            MAX_EMBED_LENGTH = 3000

            # Split report into multiple embeds if needed
            embeds = []
            current_sections = []
            current_length = 0

            # Keep track of where we split (after summary or in player list)
            summary_end_index = report_sections.index("") if "" in report_sections else 0

            for i, section in enumerate(report_sections):
                section_with_newline = section + "\n"
                section_length = len(section_with_newline)

                # Check if adding this section exceeds limit
                if current_length + section_length > MAX_EMBED_LENGTH and current_sections:
                    # Create embed with current sections
                    embed_description = "\n".join(current_sections)
                    embeds.append(embed_description)
                    current_sections = []
                    current_length = 0

                    # If we're past the summary, add a continuation header
                    if i > summary_end_index:
                        continuation_header = "👥 **PLAYER DETAILS** (continued)"
                        current_sections.append(continuation_header)
                        current_length = len(continuation_header) + 1

                current_sections.append(section)
                current_length += section_length

            # Add remaining sections
            if current_sections:
                embeds.append("\n".join(current_sections))

            # Create Discord embeds
            discord_embeds = []
            for idx, embed_desc in enumerate(embeds):
                if idx == 0:
                    # First embed gets the full title
                    embed = discord.Embed(
                        title=f"📊 Attendance Report - {alliance_name}",
                        description=embed_desc,
                        color=discord.Color.blue()
                    )
                else:
                    # Subsequent embeds get continuation title
                    embed = discord.Embed(
                        title=f"📊 Attendance Report - {alliance_name} (Page {idx + 1})",
                        description=embed_desc,
                        color=discord.Color.blue()
                    )

                # Add footer only to last embed
                if idx == len(embeds) - 1:
                    if session_id:
                        embed.set_footer(text=f"Session ID: {session_id} | {sort_footer}")
                    else:
                        embed.set_footer(text=sort_footer)

                discord_embeds.append(embed)

            # Create view with back and export buttons
            view = discord.ui.View(timeout=7200)

            # Back button - different behavior for preview vs regular mode
            if is_preview and marking_view:
                back_button = discord.ui.Button(
                    label="⬅️ Back to Marking",
                    style=discord.ButtonStyle.secondary
                )

                async def back_callback(back_interaction: discord.Interaction):
                    await marking_view.update_main_embed(back_interaction)

                back_button.callback = back_callback
                view.add_item(back_button)
            else:
                back_button = discord.ui.Button(
                    label="⬅️ Back to Sessions",
                    style=discord.ButtonStyle.secondary
                )

                async def back_callback(back_interaction: discord.Interaction):
                    await self.show_session_selection(back_interaction, alliance_id)

                back_button.callback = back_callback
                view.add_item(back_button)

            # Export button
            export_button = discord.ui.Button(
                label="Export",
                emoji="📥",
                style=discord.ButtonStyle.primary
            )

            async def export_callback(export_interaction: discord.Interaction):
                session_info = {
                    'session_name': session_name,
                    'alliance_name': alliance_name,
                    'event_type': event_type or 'Other',
                    'event_date': event_date,
                    'total_players': len(sorted_records),
                    'present_count': present_count,
                    'absent_count': absent_count,
                    'not_recorded_count': not_recorded_count
                }
                export_view = ExportFormatSelectView(self, sorted_records, session_info)
                await export_interaction.response.send_message(
                    "Select export format:",
                    view=export_view,
                    ephemeral=True
                )

            export_button.callback = export_callback
            view.add_item(export_button)

            # Post to Channel button - only for non-preview mode
            if not is_preview:
                post_button = discord.ui.Button(
                    label="Post to Channel",
                    emoji="📢",
                    style=discord.ButtonStyle.success
                )

                async def post_callback(post_interaction: discord.Interaction):
                    channel_view = ChannelSelectView(self, discord_embeds, image_file=None)
                    await post_interaction.response.send_message(
                        "Select a channel to post the attendance report:",
                        view=channel_view,
                        ephemeral=True
                    )

                post_button.callback = post_callback
                view.add_item(post_button)

            # Handle both regular and deferred interactions
            # Send first embed with view, then send additional embeds
            if interaction.response.is_done():
                await interaction.edit_original_response(embed=discord_embeds[0], view=view)
            else:
                await interaction.response.edit_message(embed=discord_embeds[0], view=view)

            # Send additional embeds as follow-up messages (without view)
            if len(discord_embeds) > 1:
                for embed in discord_embeds[1:]:
                    await interaction.followup.send(embed=embed, ephemeral=False)

        except Exception as e:
            print(f"Error showing text attendance report: {e}")
            # Try to respond appropriately based on interaction state
            error_content = "❌ An error occurred while generating attendance report."
            if interaction.response.is_done():
                await interaction.edit_original_response(content=error_content, embed=None, view=None)
            else:
                await interaction.response.edit_message(content=error_content, embed=None, view=None)

    async def show_session_selection(self, interaction: discord.Interaction, alliance_id: int):
        """Show available attendance sessions for an alliance"""
        try:
            # Get alliance name
            alliance_name = "Unknown Alliance"
            with sqlite3.connect('db/alliance.sqlite') as alliance_db:
                cursor = alliance_db.cursor()
                cursor.execute("SELECT name FROM alliance_list WHERE alliance_id = ?", (alliance_id,))
                alliance_result = cursor.fetchone()
                if alliance_result:
                    alliance_name = alliance_result[0]
        
            # Get session details from single attendance_records table
            sessions = []
            with sqlite3.connect('db/attendance.sqlite') as attendance_db:
                cursor = attendance_db.cursor()
                cursor.execute("""
                    SELECT 
                        session_id,
                        session_name,
                        event_type,
                        MIN(event_date) as session_date,
                        COUNT(DISTINCT player_id) as player_count,
                        SUM(CASE WHEN status != 'not_recorded' THEN 1 ELSE 0 END) as marked_count
                    FROM attendance_records
                    WHERE alliance_id = ?
                    GROUP BY session_id
                    ORDER BY session_date DESC
                """, (str(alliance_id),))
                
                for row in cursor.fetchall():
                    # Handle date - could be string or datetime object
                    date_value = row[3]
                    if date_value:
                        if isinstance(date_value, str):
                            date_display = date_value.split('T')[0] if 'T' in date_value else date_value.split()[0] if ' ' in date_value else date_value
                        else:
                            try:
                                date_display = date_value.strftime("%Y-%m-%d")
                            except:
                                date_display = str(date_value)
                    else:
                        date_display = "Unknown"

                    sessions.append({
                        'session_id': row[0],
                        'name': row[1],
                        'event_type': row[2],
                        'date': date_display,
                        'player_count': row[4],
                        'marked_count': row[5]
                    })

            if not sessions:
                # Create embed for no sessions found
                embed = discord.Embed(
                    title=f"📋 Attendance Sessions - {alliance_name}",
                    description=f"❌ **No attendance sessions found for {alliance_name}.**\n\nTo create attendance records, use the 'Mark Attendance' option from the main menu.",
                    color=discord.Color.orange()
                )
                
                # Add back button
                back_view = discord.ui.View(timeout=7200)
                back_button = discord.ui.Button(
                    label="⬅️ Back to Alliance Selection",
                    style=discord.ButtonStyle.secondary
                )
                
                async def back_callback(back_interaction: discord.Interaction):
                    attendance_cog = self.bot.get_cog("Attendance")
                    if attendance_cog:
                        try:
                            result = await attendance_cog._handle_permission_check(back_interaction)
                            if not result:
                                return

                            alliances, _ = result
                            alliances_with_counts = attendance_cog._get_alliances_with_counts(alliances)

                            from .attendance import AllianceSelectView
                            view = AllianceSelectView(alliances_with_counts, attendance_cog, is_marking=False)

                            select_embed = discord.Embed(
                                title="👀 View Attendance - Alliance Selection",
                                description="Please select an alliance to view attendance records:",
                                color=discord.Color.green()
                            )

                            await back_interaction.response.edit_message(embed=select_embed, view=view)
                        except Exception as e:
                            print(f"Error going back to alliance selection: {e}")
                            await attendance_cog.show_attendance_menu(back_interaction)
                
                back_button.callback = back_callback
                back_view.add_item(back_button)
                
                if interaction.response.is_done():
                    await interaction.edit_original_response(
                        content=None,
                        embed=embed,
                        view=back_view,
                        attachments=[]
                    )
                else:
                    await interaction.response.edit_message(
                        content=None,
                        embed=embed,
                        view=back_view,
                        attachments=[]
                    )
                return
        
            # Create session selection view
            view = SessionSelectView(sessions, alliance_id, self, is_viewing=True)
            
            embed = discord.Embed(
                title=f"📋 Attendance Sessions - {alliance_name}",
                description="Please select a session to view attendance records:",
                color=discord.Color.blue()
            )
            
            if interaction.response.is_done():
                await interaction.edit_original_response(embed=embed, view=view, attachments=[])
            else:
                await interaction.response.edit_message(embed=embed, view=view, attachments=[])
    
        except Exception as e:
            print(f"Error showing session selection: {e}")
            if interaction.response.is_done():
                await interaction.edit_original_response(
                    content="❌ An error occurred while loading sessions.",
                    embed=None,
                    view=None
                )
            else:
                await interaction.response.send_message(
                    "❌ An error occurred while loading sessions.",
                    ephemeral=True
                )

async def setup(bot):
    try:
        cog = AttendanceReport(bot)
        await bot.add_cog(cog)
    except Exception as e:
        print(f"❌ Failed to load AttendanceReport cog: {e}")