from collections import defaultdict
import csv


components = {
    "pi5": {
        "name": "Pi 5",
        "link": "https://thepihut.com/products/raspberry-pi-5?variant=42531604922563",
        "price": 74.40
    },
    "metal_case": {
        "name": "Metal Case",
        "link": "https://thepihut.com/products/industrial-grade-metal-case-for-raspberry-pi-5",
        "price": 5.80
    },
    "nvme_poe_hat": {
        "name": "NVMe Hat",
        "link": "https://thepihut.com/products/52pi-m-2-nvme-2280-poe-hat-for-raspberry-pi-5",
        "price": 28.80
    },
    "hq_camera": {
        "name": "HQ Camera",
        "link": "https://thepihut.com/products/raspberry-pi-high-quality-camera-module",
        "price": 45
    },
    # Add missing components so we don't get KeyErrors
    "nvme_ssd": {
        "name": "NVMe SSD",
        "link": "https://uk.insight.com/en_GB/shop/product/SDBPNTY-512G-IN/integral/SDBPNTY-512G-IN/Integral-SSD-512-GB-internal-M2-2280-PCIe-30-x4-NVMe/",
        "price": 86.39
    },
    "active_cooler": {
        "name": "Active Cooler",
        "link": "https://thepihut.com/products/active-cooler-for-raspberry-pi-5",
        "price": 4.50
    },
    "rtc_battery": {
        "name": "RTC Battery",
        "link": "https://amzn.eu/d/02OrSOBT",
        "price": 5.88
    },
    "lens": {
        "name": "Lens",
        "link": "https://thepihut.com/products/raspberry-pi-high-quality-camera-lens",
        "price": 22
    },
    "sd_card": {
        "name": "SD Card",
        "link": "https://uk.insight.com/en_GB/shop/product/SDCS3%2F64GB/kingston%20technology/SDCS3%2F64GB/Kingston-Canvas-Select-Plus-flash-memory-card-64-GB-microSDXC-UHSI/",
        "price": 8.39
    },
    "poe_hat": {
        "name": "PoE Hat",
        "link": "https://thepihut.com/products/poe-hat-for-raspberry-pi-5-with-cooling-fan",
        "price": 19.20
    },
    "ttl_hat": {
        "name": "TTL Hat",
        "link": "https://thepihut.com/products/gpio-screw-terminal-hat",
        "price": 13.00
    },
    "ethernet_cable": {
        "name": "Ethernet Cable",
        "link": "https://thepihut.com/products/cat6a-shielded-snagless-rj45-ethernet-cable-2m?variant=40638521704643&country=GB&currency=GBP&utm_medium=product_sync&utm_source=google&utm_content=sag_organic&utm_campaign=sag_organic&gad_source=1&gad_campaignid=11673057096&gbraid=0AAAAADfQ4GEC9FtD9xgscKJkD8ZGfEWVP&gclid=CjwKCAiAraXJBhBJEiwAjz7MZR3RhOtRWh0PaTDeGcQKzqcR_jbTVvi6cRdoKAOxcU7_G5725rGyQxoCxn8QAvD_BwE",
        "price": 3
    },
    "poe_switch_5": {
        "name": "TPLink 5 Port PoE Switch",
        "link": "https://uk.insight.com/en_GB/shop/product/TL-SF1005P/tp-link/TL-SF1005P/TPLink-TLSF1005P-switch-5-ports-unmanaged/",
        "price": 37.19
    },
    "poe_switch_8": {
        "name": "DLink 8 Port PoE Switch",
        "link": "https://uk.insight.com/en_GB/shop/product/DGS-1100-08PV2%2FB/d-link/DGS-1100-08PV2%2FB/DLink-DGS-110008PV2-switch-8-ports-smart/#tab-specifications#",
        "price": 67.19
    },
    "poe_switch_16": {
        "name": "TPLink 16 Port PoE+ Switch",
        "link": "https://uk.insight.com/en_GB/shop/product/TL-SG1016PE/tp-link/TL-SG1016PE/TPLink-TLSG1016PE-switch-16-ports-smart-rackmountable/",
        "price": 157.19
    },
}


devices = {
    "controller": {
        "pi5": 1,
        "metal_case": 1,
        "nvme_poe_hat": 1,
        "nvme_ssd": 1,
        "active_cooler": 1,
        "rtc_battery": 1,
    },
    "camera": {
        "pi5": 1,
        "poe_hat": 1,
        "hq_camera": 1,
        "lens": 1,
        "sd_card": 1,
    },
    "ttl": {
        "pi5": 1,
        "ttl_hat": 1,
        "poe_hat": 1,
        "sd_card": 1
    }
}


def calculate_totals(devices, module_counts):
    totals = defaultdict(int)

    for device_name, count in module_counts.items():
        if device_name not in devices:
            continue

        for component_key, qty in devices[device_name].items():
            totals[component_key] += qty * count

    return totals


def get_input(prompt: str) -> int:
    while True:
        try:
            return int(input(prompt))
        except ValueError:
            print("Please enter an integer number")


def export_shopping_list_csv(filename, totals, components):
    grand_total = 0.0

    with open(filename, mode="w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)

        # Header
        writer.writerow(["Item", "Number", "Unit Price (£)", "Total Price (£)", "Link"])

        for component_key in sorted(totals):
            qty = totals[component_key]
            component_info = components.get(component_key, {})

            name = component_info.get("name", component_key)
            price = component_info.get("price")
            link = component_info.get("link", "")

            if price is not None:
                total_price = qty * price
                grand_total += total_price
                writer.writerow([name, qty, f"{price:.2f}", f"{total_price:.2f}", link])
            else:
                writer.writerow([name, qty, "", "", link])

        # Add grand total row
        writer.writerow([])
        writer.writerow(["TOTAL", "", "", f"{grand_total:.2f}", ""])

    print(f"\nShopping list exported to {filename}")


if __name__ == "__main__":
    n_controllers = get_input("How many controllers: ")
    n_cameras = get_input("How many camera modules: ")
    n_ttl = get_input("How many TTL modules: ")  # placeholder for future

    module_counts = {
        "controller": n_controllers,
        "camera": n_cameras,
        "ttl": n_ttl
    }

    totals = calculate_totals(devices, module_counts)

    total_units = sum(module_counts.values()) + 1 # Add one for user PC connection

    # 1 Ethernet cable per unit
    totals["ethernet_cable"] += total_units

    # PoE switch sizing
    if total_units <= 8:
        totals["poe_switch_8"] += 1
    else:
        totals["poe_switch_16"] += 1

    print("\n=== Component Summary ===\n")

    grand_total = 0.0

    for component_key in sorted(totals):
        qty = totals[component_key]
        component_info = components.get(component_key)

        if not component_info:
            print(f"{component_key}: {qty} (No metadata defined)")
            continue

        name = component_info["name"]
        link = component_info.get("link")
        price = component_info.get("price")

        print(f"{name}: {qty}")

        if price is not None:
            subtotal = qty * price
            grand_total += subtotal
            print(f"  Unit Price: £{price:.2f}")
            print(f"  Subtotal:   £{subtotal:.2f}")
        else:
            print("  ⚠ No price defined")

        if link:
            print(f"  Link: {link}")

        print()

    # Processing fee (e.g. 5%)
    processing_rate = 0.05
    processing_fee = grand_total * processing_rate
    final_total = grand_total + processing_fee

    print("=== Cost Summary ===\n")
    print(f"Hardware Total:  £{grand_total:.2f}")
    print(f"Processing (5%): £{processing_fee:.2f}")
    print(f"Final Total:     £{final_total:.2f}")

    export_shopping_list_csv("shopping_list.csv", totals, components)